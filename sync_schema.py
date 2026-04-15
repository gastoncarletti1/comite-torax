from app import app, create_tables_and_admin


with app.app_context():
    create_tables_and_admin()
    print("Schema synced and admin ensured.")
