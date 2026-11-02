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
    assert connection.scalar(text("SELECT has_table_privilege(current_user,'training_profiles','SELECT')"))
    assert not connection.scalar(text("SELECT has_table_privilege(current_user,'training_profiles','UPDATE')"))
    assert not connection.scalar(text("SELECT rolsuper OR rolcreatedb OR rolcreaterole FROM pg_roles WHERE rolname=current_user"))
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
print('Reader verified: SELECT allowed; writes denied even with a read-write transaction.')
