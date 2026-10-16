from fastapi import Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    """Business error returned to the client as {"error": {"code", "message"}}."""

    def __init__(self, status_code: int, code: str, message: str | None = None):
        self.status_code = status_code
        self.code = code
        self.message = message or code.replace("_", " ").capitalize()
        super().__init__(self.message)


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )
