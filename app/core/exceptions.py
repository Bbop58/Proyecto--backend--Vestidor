from fastapi import HTTPException, status


class CredentialsException(HTTPException):
    def __init__(self, detail: str = "No se pudieron validar las credenciales"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


class InactiveUserException(HTTPException):
    def __init__(self, detail: str = "Usuario inactivo"):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail
        )


class UserAlreadyExistsException(HTTPException):
    def __init__(self, detail: str = "Ya existe un usuario con este correo electronico"):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail
        )


class TokenRevokedException(HTTPException):
    def __init__(self, detail: str = "Token revocado o invalido"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )
