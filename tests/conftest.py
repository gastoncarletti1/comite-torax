import os
import tempfile
import shutil
import io
import pytest
from app import app as flask_app, db
from app import User, Patient

@pytest.fixture(scope="session")
def temp_upload_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("uploads")
    yield str(d)
    shutil.rmtree(str(d), ignore_errors=True)


@pytest.fixture(scope="session")
def test_app(temp_upload_dir):
    # configure app for testing
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    # use a temporary sqlite file for full SQLAlchemy behavior
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    flask_app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
    flask_app.config["UPLOAD_DIR"] = temp_upload_dir

    # create tables
    with flask_app.app_context():
        db.create_all()
    yield flask_app

    # teardown
    with flask_app.app_context():
        db.session.remove()
        db.drop_all()
    os.close(db_fd)
    try:
        os.unlink(db_path)
    except Exception:
        pass


@pytest.fixture
def client(test_app):
    return test_app.test_client()


@pytest.fixture
def db_session(test_app):
    with test_app.app_context():
        db.session.remove()
        db.drop_all()
        db.create_all()
        yield db
        db.session.remove()


@pytest.fixture
def user(db_session):
    user = User(full_name="Test User", email="test@example.com", username="testuser", status="approved")
    user.set_password("secret")
    db_session.session.add(user)
    db_session.session.commit()
    return user


@pytest.fixture
def patient(db_session, user):
    p = Patient(full_name="Paciente Test")
    p.created_by = user
    db_session.session.add(p)
    db_session.session.commit()
    return p
