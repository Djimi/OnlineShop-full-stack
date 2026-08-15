"""Delivery engine exception hierarchy with stable machine-readable codes."""


class DeliveryError(Exception):
    code = "DELIVERY"


class ValidationError(DeliveryError):
    code = "VALIDATION"


class ReadError(DeliveryError):
    code = "READ_ERROR"


class AbsentResourceError(ReadError):
    code = "NOT_FOUND"


class MutationVerificationError(DeliveryError):
    code = "MUTATION_VERIFY"


class WaiterTimeoutError(DeliveryError):
    code = "WAITER_TIMEOUT"


class AmbiguousStateError(DeliveryError):
    code = "AMBIGUOUS"


class NotImplementedPhaseError(DeliveryError):
    code = "NOT_IMPLEMENTED"
