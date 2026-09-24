"""Mobile API route blueprints.

Each module exposes a ``build_*_blueprint`` factory so every app instance
gets independent, injectable views (no module-global request state).
"""

from rex.mobile_api.routes.attachments import build_attachments_blueprint
from rex.mobile_api.routes.auth import build_auth_blueprint
from rex.mobile_api.routes.chat import build_chat_blueprint
from rex.mobile_api.routes.home import build_home_blueprint
from rex.mobile_api.routes.pairing import build_pairing_blueprint
from rex.mobile_api.routes.scaffolds import build_scaffolds_blueprint
from rex.mobile_api.routes.settings import build_settings_blueprint
from rex.mobile_api.routes.status import build_status_blueprint
from rex.mobile_api.routes.strong_auth import build_strong_auth_blueprint
from rex.mobile_api.routes.voice import build_voice_blueprint

__all__ = [
    "build_attachments_blueprint",
    "build_auth_blueprint",
    "build_chat_blueprint",
    "build_home_blueprint",
    "build_pairing_blueprint",
    "build_scaffolds_blueprint",
    "build_settings_blueprint",
    "build_status_blueprint",
    "build_strong_auth_blueprint",
    "build_voice_blueprint",
]
