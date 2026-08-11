"""Route blueprints, grouped by what they are for."""

from .admin import admin_bp
from .auth import auth_bp
from .main import main_bp
from .records import records_bp
from .recognition import recognition_bp

__all__ = ["admin_bp", "auth_bp", "main_bp", "records_bp", "recognition_bp"]
