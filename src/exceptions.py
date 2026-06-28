class RakutenBotError(Exception):
    pass


class ConfigError(RakutenBotError):
    pass


class StockCheckError(RakutenBotError):
    pass


class LoginError(RakutenBotError):
    pass


class CartError(RakutenBotError):
    pass


class CheckoutError(RakutenBotError):
    pass


class NotificationError(RakutenBotError):
    pass
