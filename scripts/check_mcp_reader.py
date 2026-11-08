"""Run inside MCP container to verify database privilege enforcement without modifying rows."""
import os
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
engine=create_engine(os.environ['DATABASE_URL'])
with engine.connect() as connection:
    assert connection.scalar(text('SHOW transaction_read_only')) == 'on'
    assert connection.scalar(text("SELECT has_table_privilege(current_user,'wearable_records','SELECT')"))
    assert not connection.scalar(text("SELECT has_table_privilege(current_user,'wearable_records','UPDATE')"))
    assert not connection.scalar(text("SELECT has_table_privilege(current_user,'sync_jobs','INSERT')"))
    for table in ('suunto_sync_jobs', 'suunto_quota'):
        assert connection.scalar(text(f"SELECT has_table_privilege(current_user,'{table}','SELECT')"))
        assert not connection.scalar(text(f"SELECT has_table_privilege(current_user,'{table}','INSERT,UPDATE,DELETE')"))
    assert connection.scalar(text("SELECT has_table_privilege(current_user,'training_profiles','SELECT')"))
    assert not connection.scalar(text("SELECT has_table_privilege(current_user,'training_profiles','UPDATE')"))
    assert not connection.scalar(text("SELECT rolsuper OR rolcreatedb OR rolcreaterole FROM pg_roles WHERE rolname=current_user"))
    assert connection.scalar(text("SELECT has_table_privilege(current_user,'race_plans','UPDATE')"))
    assert connection.scalar(text("SELECT has_table_privilege(current_user,'plan_audit','INSERT')"))
    assert not connection.scalar(text("SELECT has_table_privilege(current_user,'plan_audit','UPDATE')"))
    assert not connection.scalar(text("SELECT has_table_privilege(current_user,'race_plans','DELETE')"))
    connection.rollback()
    connection.execute(text('SET TRANSACTION READ WRITE'))
    # Synthetic plan writes are rolled back; no real plan or health row is touched.
    connection.execute(text("INSERT INTO race_plans(owner,settings,sessions,revision,updated_at) VALUES ('mcp-permission-check', '{}', '[]', 1, now())"))
    connection.execute(text("UPDATE race_plans SET revision=2 WHERE owner='mcp-permission-check'"))
    connection.execute(text("INSERT INTO plan_audit(id,owner,revision,event,detail,created_at) VALUES ('mcp-permission-check','mcp-permission-check',2,'test','{}',now())"))
    connection.rollback()
    connection.execute(text('SET TRANSACTION READ WRITE'))
    try:
        connection.execute(text('UPDATE wearable_records SET owner=owner WHERE FALSE'))
    except DBAPIError as error:
        assert getattr(error.orig,'sqlstate',None)=='42501','Expected permission denied'
        connection.rollback()
    else:
        connection.rollback()
        raise AssertionError('Reader unexpectedly has UPDATE access')
print('MCP role verified: health/profile writes denied; isolated plan/audit grants only; default transactions read-only.')
