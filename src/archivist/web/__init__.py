"""Archivist web application."""

from pathlib import Path

from flask import Flask


def create_app(mode: str = "dev") -> Flask:
    """Create the Flask application.

    Args:
        mode: 'dev' for local development (all features),
              'search' for production server (search API only).
    """
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
    )
    app.config["MODE"] = mode
    app.config["SECRET_KEY"] = "dev-secret-key"  # Override from config.yaml in production

    # Make mode + css version available in all templates
    @app.context_processor
    def inject_globals():
        css_path = Path(__file__).parent / "static" / "style.css"
        css_version = int(css_path.stat().st_mtime) if css_path.exists() else 1
        return {"mode": mode, "css_version": css_version}

    # Register blueprints
    from archivist.web.routes.reading import reading_bp
    app.register_blueprint(reading_bp)

    # Root redirect
    @app.route("/")
    def index():
        from flask import redirect
        return redirect("/reading/")

    return app
