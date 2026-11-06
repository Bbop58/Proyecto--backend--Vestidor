"""Private object storage; no client-provided paths or credentials."""
import hashlib
import re
from app.config import settings

MAX_BYTES = 10 * 1024 * 1024


class StorageError(Exception):
    pass


def storage_ready():
    return all(getattr(settings, name, None) for name in (
        'BACKUP_S3_ENDPOINT', 'BACKUP_S3_BUCKET', 'BACKUP_S3_ACCESS_KEY_ID',
        'BACKUP_S3_SECRET_ACCESS_KEY', 'BACKUP_ENVIRONMENT')) and bool(
            re.fullmatch(r'[a-zA-Z0-9_-]{1,40}', settings.BACKUP_ENVIRONMENT)) and settings.BACKUP_S3_ENDPOINT.startswith('https://')


class BackupStorage:
    def __init__(self, client, bucket):
        self.client, self.bucket = client, bucket

    @classmethod
    def configured(cls):
        if not storage_ready():
            raise StorageError('STORAGE_NOT_CONFIGURED')
        import boto3
        from botocore.config import Config
        def private(value):
            return value.get_secret_value() if hasattr(value, 'get_secret_value') else value
        client = boto3.client('s3', endpoint_url=settings.BACKUP_S3_ENDPOINT,
            region_name=settings.BACKUP_S3_REGION,
            aws_access_key_id=private(settings.BACKUP_S3_ACCESS_KEY_ID),
            aws_secret_access_key=private(settings.BACKUP_S3_SECRET_ACCESS_KEY),
            config=Config(connect_timeout=5, read_timeout=10, retries={'total_max_attempts': 1},
                signature_version='s3v4', s3={'addressing_style': settings.BACKUP_S3_ADDRESSING_STYLE}))
        return cls(client, settings.BACKUP_S3_BUCKET)

    def put_verified(self, key, content, metadata):
        if not 0 < len(content) <= MAX_BYTES:
            raise StorageError('SIZE_LIMIT')
        checksum = hashlib.sha256(content).hexdigest()
        try:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=content,
                ContentType='application/json', Metadata={**metadata, 'sha256': checksum})
            self.get_verified(key, checksum)
        except Exception as error:
            raise StorageError('UPLOAD_FAILED') from error
        return checksum

    def get_verified(self, key, checksum):
        body = None
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            body = response['Body']
            if not 0 < response.get('ContentLength', MAX_BYTES + 1) <= MAX_BYTES:
                raise StorageError('SIZE_LIMIT')
            content = body.read(MAX_BYTES + 1)
            if len(content) > MAX_BYTES or hashlib.sha256(content).hexdigest() != checksum:
                raise StorageError('INTEGRITY_FAILED')
            return content
        except StorageError:
            raise
        except Exception as error:
            raise StorageError('DOWNLOAD_FAILED') from error
        finally:
            if body is not None:
                body.close()

    def delete(self, key):
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as error:
            raise StorageError('DELETE_FAILED') from error

    def close(self):
        if hasattr(self.client, 'close'):
            self.client.close()
