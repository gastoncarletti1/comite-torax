from app import app, db, User, create_tables_and_admin

with app.app_context():
    create_tables_and_admin()
    admin = User.query.filter_by(username="admin").first()
    print("Admin encontrado:", admin)

    if admin is None:
        admin = User(
            full_name="Administrador Comité",
            specialty="",
            email="admin@comite.com",
            username="admin",
            role="admin",
            status="approved",
        )
        admin.set_password("Admin2025!")
        db.session.add(admin)
        print("✅ Admin creado de cero.")
    else:
        admin.set_password("Admin2025!")
        admin.role = "admin"
        admin.status = "approved"
        print("✅ Admin actualizado. Usuario: admin / Pass: Admin2025!")

    db.session.commit()
    print("💾 Cambios guardados en la base de datos.")
