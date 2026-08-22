# Mobile Client Surfaces

Oracle's supported mobile-access surface is the responsive browser UI. It uses
the same reusable `house_ui/` sources and Brain HTTP contracts as desktop
browsers; a household does not need a native application, app-store package, or
mobile SDK to use it.

The browser remains a thin client. Routing, dispatch, capability behavior,
configuration authority, and final reply shaping remain on the Brain. Transport
exposure and canonical access policy determine whether a particular household
permits host-local, LAN, or later externally bounded browser access.

## Responsive Web Surface

The reusable web client provides responsive navigation and household pages for
home, weather, calendar, audio, house controls, and bounded recovery surfaces.
It reads the declared HTTP payloads, submits structured actions, and renders
optional canonical household UI links. Empty optional data remains an ordinary
supported state.

The Stage 4 minimal installation proves one basic web surface locally. Broader
LAN/mobile reachability remains an explicit ingress and access-policy choice; it
does not require a different client implementation.

## Historical Native Proof Of Concept

A historical Expo/React Native Android/iOS proof of concept remains in private
development history. It is unused, is not a current mobile-access path, and is
not a supported core component, standard-installation profile, release artifact,
or clean-core CI dependency. Its private package identity creates no reusable
compatibility contract.

Stage 4 does not rename its package, preserve native update continuity, build or
package it, or migrate an installed application. The historical sources may
remain privately available as implementation reference without entering the
clean reusable distribution.

## Future Native Application

Any future native Oracle application is a new explicitly scoped project. Its
package identity, platform support, client contracts, authentication, update
continuity, dependencies, build pipeline, release signing, and compatibility
surface will be selected and validated when that project begins. The historical
proof of concept creates no default or migration obligation for that work.
