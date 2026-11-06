"""One provider capture cannot fund two distinct commercial/platform records."""
import hashlib
from sqlalchemy import select,func

def lock_contract_tables(connection):
    """Match backup's table-before-parent ordering; row writers remain concurrent."""
    from app.models import Tenant,Subscription,SubscriptionTerm,SubscriptionChangeRequest
    from app.models.subscription_payment import SubscriptionPayment
    names=[]
    for model in (Tenant,Subscription,SubscriptionTerm,SubscriptionChangeRequest,SubscriptionPayment):
        table=model.__table__;schema=connection.schema_for_object(table) or connection.execute(select(func.current_schema())).scalar_one()
        quote=connection.dialect.identifier_preparer
        names.append(quote.quote_schema(schema)+'.'+quote.quote(table.name))
    connection.exec_driver_sql('LOCK TABLE '+','.join(sorted(names))+' IN ROW EXCLUSIVE MODE')

def capture_claimed_elsewhere(db,capture_id,*,commercial_id=None,billing_id=None):
    from app.models.payment_intent import PaymentIntent
    from app.models.subscription_payment import SubscriptionPayment
    lock=int.from_bytes(hashlib.sha256(('paypal-capture:'+capture_id).encode()).digest()[:8],'big',signed=True)
    db.connection().execute(select(func.pg_advisory_xact_lock(lock)))
    for model,own in ((PaymentIntent,commercial_id),(SubscriptionPayment,billing_id)):
        query=select(model.id).where(model.capture_id==capture_id)
        if own is not None:query=query.where(model.id!=own)
        if db.connection().execute(query).first():return True
    return False
