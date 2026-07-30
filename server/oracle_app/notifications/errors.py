class NotificationRequestError(ValueError):
    pass


class NotificationDefinitionNotFoundError(NotificationRequestError):
    pass


class NotificationContextNotSupportedError(NotificationRequestError):
    pass


class NotificationSuppressionUnavailableError(RuntimeError):
    pass
