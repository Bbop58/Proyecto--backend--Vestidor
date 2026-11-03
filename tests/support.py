def make_test_admin(db,email):
    """Fixture-only elevation; public registration always creates a customer."""
    from app.models.user import User
    from app.models.role import Role
    user=db.query(User).filter_by(email=email).one()
    user.role_id=db.query(Role).filter_by(name="admin",tenant_id=None).one().id
    db.commit()
