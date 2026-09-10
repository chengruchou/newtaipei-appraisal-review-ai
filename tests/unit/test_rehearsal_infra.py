"""Offline infrastructure invariants; these checks do not establish deployed telemetry."""

import json
from pathlib import Path

STACK = Path(__file__).resolve().parents[2] / "infra/rehearsal/stack.json"


def test_rehearsal_stack_is_observation_only_and_exactly_scoped():
    template = json.loads(STACK.read_text())
    assert len(STACK.read_bytes()) < 51_200
    assert {resource["Type"] for resource in template["Resources"].values()} == {
        "AWS::CloudWatch::Alarm",
        "AWS::CloudWatch::Dashboard",
        "AWS::Logs::QueryDefinition",
    }
    alarms = [
        resource["Properties"]
        for resource in template["Resources"].values()
        if resource["Type"] == "AWS::CloudWatch::Alarm"
    ]
    assert len(alarms) == 9
    for alarm in alarms:
        assert alarm["AlarmActions"] == [{"Ref": "AlarmTopicArn"}]
        assert alarm["InsufficientDataActions"] == [{"Ref": "AlarmTopicArn"}]
        assert alarm["TreatMissingData"] in {"missing", "breaching"}
        assert alarm["DatapointsToAlarm"] == 2
        assert alarm["EvaluationPeriods"] == 3
        assert {tag["Key"] for tag in alarm["Tags"]} >= {
            "owner",
            "expiry",
            "campaign",
            "pull-request",
        }
        if alarm["Namespace"] == "AppraisalReview/Rehearsal":
            assert alarm["Dimensions"] == [
                {"Name": name, "Value": {"Ref": name}}
                for name in ("CampaignId", "CodeCommit", "ImageDigest")
            ]
    for name in ("JobLatencyAlarm", "ModelLatencyAlarm"):
        assert template["Resources"][name]["Properties"]["ExtendedStatistic"] == "p99"
    heartbeat = template["Resources"]["CollectorHeartbeatAlarm"]["Properties"]
    assert heartbeat["TreatMissingData"] == "breaching"
    assert heartbeat["ComparisonOperator"] == "LessThanThreshold"


def test_dashboard_and_query_do_not_project_raw_messages_or_mix_campaigns():
    template = json.loads(STACK.read_text())
    dashboard = json.loads(
        template["Resources"]["RehearsalDashboard"]["Properties"]["DashboardBody"]["Fn::Sub"]
    )
    query = template["Resources"]["RehearsalQuery"]["Properties"]
    assert query["LogGroupNames"] == [{"Ref": "RuntimeLogGroupName"}]
    text = query["QueryString"]["Fn::Sub"]
    assert "@message" not in text and "fields *" not in text
    assert "stats count(*)" in text
    for binding in ("CampaignId", "CodeCommit", "ImageDigest"):
        assert "${" + binding + "}" in text
    metrics = [
        metric[1]
        for widget in dashboard["widgets"]
        if widget["type"] == "metric"
        for metric in widget["properties"]["metrics"]
    ]
    assert set(metrics) >= {
        "ApproximateAgeOfOldestMessage",
        "ApproximateNumberOfMessagesVisible",
        "SucceededJobs",
        "FailedJobs",
        "Retries",
        "WaitingTasks",
        "ExpiredLeases",
        "JobLatency",
        "ModelLatency",
        "InputTokens",
        "OutputTokens",
        "EstimatedCostMicrounits",
        "CollectorHeartbeat",
    }
