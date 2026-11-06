from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

class SecretSafeRoute(APIRoute):
    def get_route_handler(self):
        handler=super().get_route_handler()
        async def safe(request):
            try:return await handler(request)
            except RequestValidationError:
                raise HTTPException(422,'Solicitud no válida; revisa los campos') from None
        return safe
