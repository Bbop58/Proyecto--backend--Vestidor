"""Session restoration and profile edits preserve the same company branding as login."""
import pytest
from tests.test_tenant_sales import shops
from tests.test_tenant_contract import headers
from app.models.tenant import Tenant


@pytest.mark.parametrize('method',['GET','PUT'])
def test_profile_preserves_company_branding(client,db_session,shops,method):
    user,_,_=shops['a']
    company=db_session.get(Tenant,user.tenant_id)
    company.color_primario='#126633'
    company.eslogan='Ropa para cada día'
    db_session.commit()
    kwargs={'json':{'full_name':'QA editado'}} if method=='PUT' else {}
    response=client.request(method,'/api/v1/users/me',headers=headers(user),**kwargs)
    assert response.status_code==200,response.text
    body=response.json()
    assert body['tenant_id']==str(company.id)
    assert body['tenant_name']==company.nombre
    assert body['tenant_color_primario']=='#126633'
    assert body['tenant_eslogan']=='Ropa para cada día'
    assert body['permissions']
