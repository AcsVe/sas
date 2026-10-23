from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from flask_migrate import Migrate

db = SQLAlchemy()
csrf = CSRFProtect()
migrate = Migrate()
