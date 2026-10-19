"""Archivist web application."""

from hashlib import sha256
from pathlib import Path

from flask import Flask


def static_asset_version(filename: str) -> str:
    """Change an asset's cache key only when its own content changes."""
    asset_path = Path(__file__).parent / "static" / filename
    return sha256(asset_path.read_bytes()).hexdigest()[:12]


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

    # Share content-based asset versions with the static builder.
    @app.context_processor
    def inject_globals():
        return {
            "mode": mode,
            "css_version": static_asset_version("style.css"),
            "paper_search_version": static_asset_version("paper-search.js"),
            "reading_list_css_version": static_asset_version("reading-list.css"),
            "reading_detail_css_version": static_asset_version("reading-detail.css"),
            "reading_detail_js_version": static_asset_version("reading-detail.js"),
        }

    # Register blueprints
    from archivist.web.routes.reading import reading_bp
    app.register_blueprint(reading_bp)
    from archivist.web.routes.site import site_bp
    app.register_blueprint(site_bp)

    # Root redirect
    @app.route("/")
    def index():
        from flask import redirect
        return redirect("/reading/")

    return app
