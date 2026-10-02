from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import wave

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import tts
from oracle_app.application_speech import synthesize_speech_with_provider
from oracle_app.configuration.brain_core_runtime_consumers import _build_tts_provider
from oracle_app.configuration.runtime_models import PocketProvider, TtsRole
from oracle_app.schemas import TtsRequest


def wav_bytes():
    out = io.BytesIO()
    with wave.open(out, 'wb') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(24000)
        f.writeframes(b'\x01\x00' * 240)
    return out.getvalue()


@pytest.fixture
def pocket(tmp_path, monkeypatch):
    weights = tmp_path / 'model.safetensors'
    tokenizer = tmp_path / 'tokenizer.json'
    voice = tmp_path / 'voice.safetensors'
    config = tmp_path / 'model.yaml'
    for path in (weights, tokenizer, voice):
        path.write_bytes(path.name.encode())
    config.write_text(json.dumps({'weights_path': str(weights), 'flow_lm': {
        'lookup_table': {'tokenizer_path': str(tokenizer)}}, 'mimi': {}}))
    monkeypatch.setattr(tts, 'PREGENERATED_DIR', tmp_path / 'cache')
    monkeypatch.setattr(tts.importlib.metadata, 'version', lambda name: 'test-version')
    monkeypatch.setattr(tts.importlib.util, 'find_spec', lambda name: object())
    provider = tts.PocketTtsProvider(str(config), str(voice))
    model = SimpleNamespace(sample_rate=24000, get_state_for_audio_prompt=Mock(return_value={'state': 1}),
                            generate_audio=Mock(return_value=object()))
    monkeypatch.setattr(provider, '_load_model', Mock(return_value=model))
    monkeypatch.setattr(provider, '_encode_audio', lambda audio, rate: wav_bytes())
    return provider, model, voice, weights


def test_typed_selection_keeps_piper_pocket_and_disabled():
    definitions = {'piper': {'type': 'piper', 'binary_path': 'bin/piper', 'model_path': 'voice.onnx'},
                   'pocket': {'type': 'pocket', 'model_config_path': 'model.yaml', 'voice_state_path': 'voice.safetensors'}}
    for selected, expected in [('pocket', tts.PocketTtsProvider), ('piper', tts.PiperTtsProvider),
                               ('pocket', tts.PocketTtsProvider)]:
        role = TtsRole.model_validate({'enabled': True, 'provider': selected, 'providers': definitions})
        settings = SimpleNamespace(tts=SimpleNamespace(enabled=True, provider=role.providers[role.provider]))
        provider = _build_tts_provider(settings)
        assert isinstance(provider, expected)
        if selected == 'pocket':
            assert provider._model is None and not provider._warmup_started
    disabled = _build_tts_provider(SimpleNamespace(tts=SimpleNamespace(enabled=False)))
    assert isinstance(disabled, tts.DisabledTtsProvider)
    assert not disabled.status().available
    with pytest.raises(tts.TtsError, match='disabled'):
        disabled.synthesize('Hello')
    with pytest.raises(ValidationError):
        PocketProvider(type='pocket', model_config_path='model.yaml', voice_state_path='voice', temperature=0.7)
    with pytest.raises(ValidationError):
        TtsRole(enabled=True, provider='missing', providers=definitions)


def test_construction_and_status_do_not_load_model(pocket):
    provider, model, _, _ = pocket
    assert provider.status().configured
    assert not provider.status().available
    provider._load_model.assert_not_called()
    model.generate_audio.assert_not_called()


def test_explicit_warmup_and_lazy_synthesis_reuse_residency(pocket):
    provider, model, _, _ = pocket
    provider.warmup()
    assert provider.status().available
    provider.synthesize('First.')
    provider.synthesize('Second.')
    provider.warmup()
    provider._load_model.assert_called_once()
    model.get_state_for_audio_prompt.assert_called_once()
    assert model.generate_audio.call_count == 2
    assert all(call.kwargs == {'copy_state': True} for call in model.generate_audio.call_args_list)


def test_speech_edge_stays_provider_neutral_and_cache_survives_restart(pocket):
    provider, model, _, _ = pocket
    response = synthesize_speech_with_provider(TtsRequest(text='Hello.'), provider)
    assert response.media_type == 'audio/wav'
    assert response.headers['X-Oracle-TTS-Provider'] == 'pocket'
    assert response.body == wav_bytes()
    restarted = tts.PocketTtsProvider(provider.model, provider.voice_state_path)
    result = restarted.synthesize('Hello.')
    assert result.provider == 'pocket-cache' and result.audio_bytes == response.body
    assert restarted._model is None
    assert model.generate_audio.call_count == 1


def test_piper_v2_cache_identity_and_provider_coexistence_are_preserved(pocket, monkeypatch):
    pocket_provider, _, _, _ = pocket
    piper = tts.PiperTtsProvider('piper', str(Path(pocket_provider.model).parent / 'voice.onnx'))
    Path(piper.config).write_text('{"speaker":1}')
    text = 'Done.'
    old_payload = {'cache_version': 2, 'text': text, 'provider': 'piper', 'model': piper.model,
                   'configuration': {'binary': 'piper', 'config_path': piper.config,
                                     'config_sha256': hashlib.sha256(Path(piper.config).read_bytes()).hexdigest()}}
    old_key = hashlib.sha256(json.dumps(old_payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    assert piper._cache_path_for_text(text).name == f'v2-{old_key}.wav'
    assert pocket_provider._cache_path_for_text(text) != piper._cache_path_for_text(text)
    piper._store_cached_clip(text, wav_bytes())
    assert pocket_provider.synthesize(text).provider == 'pocket'
    assert piper.synthesize(text).provider == 'piper-cache'
    assert pocket_provider.synthesize(text).provider == 'pocket-cache'
    assert len(list(tts.PREGENERATED_DIR.glob('v2-*.wav'))) == 2


@pytest.mark.parametrize('asset', ['voice', 'weights', 'config', 'tokenizer', 'runtime'])
def test_changed_synthesis_identity_cannot_reuse_cached_clip(pocket, monkeypatch, asset):
    provider, model, voice, weights = pocket
    provider.synthesize('Done.')
    before = provider._cache_path_for_text('Done.')
    if asset == 'runtime':
        monkeypatch.setattr(tts.importlib.metadata, 'version', lambda name: 'new-version')
    else:
        path = {'voice': voice, 'weights': weights, 'config': Path(provider.model),
                'tokenizer': voice.parent / 'tokenizer.json'}[asset]
        with path.open('ab') as f:
            f.write(b' ')
        assert not provider.status().available
    assert provider._cache_path_for_text('Done.') != before
    assert provider.synthesize('Done.').provider == 'pocket'
    assert model.generate_audio.call_count == 2
    assert provider._load_model.call_count == 2


def test_dependency_voice_and_load_failure_are_honest(pocket, monkeypatch):
    provider, model, voice, _ = pocket
    monkeypatch.setattr(tts.importlib.util, 'find_spec', lambda name: None)
    assert 'dependency/runtime' in provider.status().detail
    monkeypatch.setattr(tts.importlib.util, 'find_spec', lambda name: object())
    voice.unlink()
    assert 'asset unavailable' in provider.status().detail
    with pytest.raises(tts.TtsError):
        provider.synthesize('Hello.')
    voice.write_bytes(b'voice')
    provider._load_model.side_effect = RuntimeError('failure')
    provider._run_warmup()
    assert not provider.status().available and 'loading failed' in provider.status().detail
    with pytest.raises(HTTPException) as error:
        synthesize_speech_with_provider(TtsRequest(text='Hello.'), provider)
    assert error.value.status_code == 503


def test_concurrent_requests_serialize_and_leave_atomic_cache(pocket):
    provider, model, _, _ = pocket
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(provider.synthesize, ['Consecutive.'] * 16))
    assert all(result.audio_bytes == wav_bytes() for result in results)
    model.generate_audio.assert_called_once()
    provider._load_model.assert_called_once()
    assert len(list(tts.PREGENERATED_DIR.iterdir())) == 1
    assert not list(tts.PREGENERATED_DIR.glob('*.tmp'))


def test_health_reports_asset_loss_during_probe_without_loading(pocket, monkeypatch):
    provider, model, _, _ = pocket
    provider.warmup()
    monkeypatch.setattr(provider, '_asset_signature', Mock(side_effect=FileNotFoundError))
    assert not provider.status().available
    assert 'asset unavailable' in provider.status().detail
    provider._load_model.assert_called_once()
    model.generate_audio.assert_not_called()


def test_pocket_uses_existing_expiry_lru_and_maintenance(pocket, monkeypatch):
    provider, _, _, _ = pocket
    monkeypatch.setattr(tts, 'TTS_CACHE_MAX_CLIPS', 1)
    provider.synthesize('First.')
    first = provider._cache_path_for_text('First.')
    provider.synthesize('Second.')
    assert not first.exists() and provider.cache_diagnostics().entry_count == 1
    assert provider.maintain_cache(now=10**12).removed_expired == 1


def test_startup_warmup_is_explicit_and_started_once(pocket, monkeypatch):
    provider, _, _, _ = pocket
    thread = Mock()
    monkeypatch.setattr(tts.threading, 'Thread', Mock(return_value=thread))
    tts.attempt_tts_provider_warmup(provider)
    tts.attempt_tts_provider_warmup(provider)
    thread.start.assert_called_once()
    provider._load_model.assert_not_called()


def test_remote_model_assets_are_rejected_before_runtime_loading(pocket):
    provider, _, _, _ = pocket
    config = json.loads(Path(provider.model).read_text())
    config['weights_path'] = 'hf://remote/model.safetensors'
    Path(provider.model).write_text(json.dumps(config))
    with pytest.raises(tts.TtsError, match='absolute local'):
        provider.warmup()
    provider._load_model.assert_not_called()
