"""One central DynamoDB owner slot shared by every admitted team sender."""

from typing import Any

from boto3.dynamodb.types import TypeSerializer

from appraisal_review.ports.model_dispatch import DispatchDenied


class DynamoDBModelDispatchStore:
    """Low-level thread-safe client; no global tables, TTL or automatic owner takeover.

    The trusted composition supplies a single regional table with a string pk.
    Low-level serialization matches the existing AWS client factory interface.
    """

    def __init__(self, client: Any, *, table_name: str) -> None:
        if not table_name:
            raise ValueError("A central dispatch table is required")
        self.client, self.table_name = client, table_name

    @staticmethod
    def _value(value: str) -> dict[str, Any]:
        return dict(TypeSerializer().serialize(value))

    def try_acquire(self, scope: str, owner: str) -> bool:
        try:
            self.client.update_item(
                TableName=self.table_name,
                Key={"pk": self._value(f"model-dispatch:{scope}")},
                UpdateExpression="SET #owner = :owner",
                ConditionExpression="attribute_not_exists(#owner)",
                ExpressionAttributeNames={"#owner": "owner"},
                ExpressionAttributeValues={":owner": self._value(owner)},
            )
        except self.client.exceptions.ConditionalCheckFailedException:
            return False
        return True

    def release(self, scope: str, owner: str) -> None:
        try:
            self.client.update_item(
                TableName=self.table_name,
                Key={"pk": self._value(f"model-dispatch:{scope}")},
                UpdateExpression="REMOVE #owner",
                ConditionExpression="#owner = :owner",
                ExpressionAttributeNames={"#owner": "owner"},
                ExpressionAttributeValues={":owner": self._value(owner)},
            )
        except self.client.exceptions.ConditionalCheckFailedException:
            raise DispatchDenied("dispatch_owner_fenced") from None
