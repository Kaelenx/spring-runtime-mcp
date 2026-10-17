"""Exercise the real handler on a 2,500-Bean payload before pagination."""

import asyncio

from spring_runtime_mcp.models.source_catalog import SourceCatalog
from spring_runtime_mcp.policy.sanitization import SanitizerPipeline
from spring_runtime_mcp.replay import FixtureReader
from spring_runtime_mcp.snapshots import SnapshotStore
from spring_runtime_mcp.tools.inspect_beans import Services, inspect_beans

from .assertions import assert_contract


def test_2500_beans_filter_pagination_and_full_frame_budget(load_sample):
    payload = load_sample("large/beans-2500.json")
    assert len(payload["contexts"]["application"]["beans"]) == 2500
    reader = FixtureReader({"beans": payload})
    services = Services(
        reader,
        SourceCatalog("local", None, {}, (), ()),
        SanitizerPipeline(),
        SnapshotStore(),
    )
    arguments = {"beanNamePattern": "service*", "maxDepth": 0}
    result = asyncio.run(inspect_beans(arguments, services))
    assert_contract("springboot_inspect_beans", result)
    assert result["totalMatched"] == 2500
    assert result["truncated"] is True and result["nextCursor"]
    assert 0 < len(result["data"]["items"]) <= 50
    ids = {b["id"] for b in result["data"]["items"]}
    for _ in range(2):
        result = asyncio.run(
            inspect_beans({**arguments, "cursor": result["nextCursor"]}, services)
        )
        assert_contract("springboot_inspect_beans", result)
        assert result["totalMatched"] == 2500
        page_ids = {b["id"] for b in result["data"]["items"]}
        assert ids.isdisjoint(page_ids)
        ids.update(page_ids)
    assert reader.calls == ["beans"]
    filtered = asyncio.run(
        inspect_beans(
            {
                "beanNamePattern": "service2499",
                "maxDepth": 2,
                "direction": "dependencies",
            },
            services,
        )
    )
    assert_contract("springboot_inspect_beans", filtered)
    assert filtered["totalMatched"] == 1 and filtered["nextCursor"] is None
    item = filtered["data"]["items"][0]
    assert item["name"] == "service2499"
    assert [edge["targetName"] for edge in item["dependencies"]] == [
        "service2498",
        "service2497",
    ]


def test_2500_beans_default_depth_preserves_known_records_with_bounded_associations(
    load_sample,
):
    reader = FixtureReader({"beans": load_sample("large/beans-2500.json")})
    services = Services(
        reader,
        SourceCatalog("local", None, {}, (), ()),
        SanitizerPipeline(),
        SnapshotStore(),
    )
    result = asyncio.run(inspect_beans({"beanNamePattern": "service*"}, services))
    assert_contract("springboot_inspect_beans", result)
    assert result["status"] == "partial"
    assert result["totalMatched"] == 2500
    assert result["data"]["items"] and result["truncated"] is True
    assert "ASSOCIATION_LIMIT" in result["coverage"]["gaps"]
