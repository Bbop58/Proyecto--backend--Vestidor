import hashlib
import io
import pytest
from app.services.backup_storage import BackupStorage, StorageError, MAX_BYTES


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.fail_put = False
        self.fail_delete = False

    def put_object(self, Bucket, Key, Body, **kwargs):
        if self.fail_put:
            raise RuntimeError('synthetic provider failure')
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):
        body = self.objects[Key]
        return {'ContentLength': len(body), 'Body': io.BytesIO(body)}

    def delete_object(self, Bucket, Key):
        if self.fail_delete:
            raise RuntimeError('synthetic delete failure')
        self.objects.pop(Key, None)


def test_roundtrip_checks_integrity_and_delete():
    client = FakeS3()
    store = BackupStorage(client, 'private-test')
    checksum = hashlib.sha256(b'{}').hexdigest()
    store.put_verified('qa/company/copy.json', b'{}', {})
    assert store.get_verified('qa/company/copy.json', checksum) == b'{}'
    client.objects['qa/company/copy.json'] = b'corrupt'
    with pytest.raises(StorageError):
        store.get_verified('qa/company/copy.json', checksum)
    store.delete('qa/company/copy.json')
    assert not client.objects


def test_failed_upload_or_oversize_is_not_success():
    client = FakeS3()
    store = BackupStorage(client, 'private-test')
    client.fail_put = True
    with pytest.raises(StorageError):
        store.put_verified('qa/x.json', b'{}', {})
    client.fail_put = False
    with pytest.raises(StorageError):
        store.put_verified('qa/x.json', b'x' * (MAX_BYTES + 1), {})
    assert not client.objects


def test_download_size_limit_and_missing_object():
    client = FakeS3()
    store = BackupStorage(client, 'private-test')
    with pytest.raises(StorageError):
        store.get_verified('missing', 'checksum')
    client.objects['huge'] = b'x' * (MAX_BYTES + 1)
    with pytest.raises(StorageError):
        store.get_verified('huge', 'checksum')


def test_new_provider_bucket_uses_virtual_host_address_before_network(monkeypatch):
    import boto3
    from botocore.config import Config
    from app.config import settings
    from pydantic import SecretStr
    monkeypatch.setattr(settings, 'BACKUP_S3_ENDPOINT', 'https://t3.storageapi.dev')
    monkeypatch.setattr(settings, 'BACKUP_S3_BUCKET', 'qa-provider-bucket')
    monkeypatch.setattr(settings, 'BACKUP_S3_ACCESS_KEY_ID', SecretStr('QA_ONLY'))
    monkeypatch.setattr(settings, 'BACKUP_S3_SECRET_ACCESS_KEY', SecretStr('QA_ONLY'))
    monkeypatch.setattr(settings, 'BACKUP_ENVIRONMENT', 'qa')
    store = BackupStorage.configured()
    addresses = []
    def stop_before_network(request, **kwargs):
        addresses.append(request.url)
        raise RuntimeError('stop-before-network')
    store.client.meta.events.register('before-send.s3.PutObject', stop_before_network)
    try:
        with pytest.raises(StorageError):
            store.put_verified('qa.json', b'{}', {})
        assert addresses == ['https://qa-provider-bucket.t3.storageapi.dev/qa.json']
    finally:
        store.close()


def test_legacy_provider_path_style_is_explicitly_configurable(monkeypatch):
    from app.config import settings
    from pydantic import SecretStr
    monkeypatch.setattr(settings, 'BACKUP_S3_ENDPOINT', 'https://qa-storage.invalid')
    monkeypatch.setattr(settings, 'BACKUP_S3_BUCKET', 'qa-bucket')
    monkeypatch.setattr(settings, 'BACKUP_S3_ACCESS_KEY_ID', SecretStr('QA_ONLY'))
    monkeypatch.setattr(settings, 'BACKUP_S3_SECRET_ACCESS_KEY', SecretStr('QA_ONLY'))
    monkeypatch.setattr(settings, 'BACKUP_ENVIRONMENT', 'qa')
    monkeypatch.setattr(settings, 'BACKUP_S3_ADDRESSING_STYLE', 'path')
    store = BackupStorage.configured()
    assert store.client.meta.config.s3['addressing_style'] == 'path'
    assert store.client.meta.config.signature_version == 's3v4'
    store.close()
