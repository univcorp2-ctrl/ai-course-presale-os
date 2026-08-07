from courseforge.models import SourceDocument
from courseforge.sources import deduplicate_documents


def test_deduplicate_documents_uses_stable_fingerprint() -> None:
    first = SourceDocument(
        id="1",
        source_type="rss",
        title="Same",
        text="Identical body",
        url="https://example.com/a",
    )
    second = SourceDocument(
        id="2",
        source_type="rss",
        title="Same",
        text="Identical body",
        url="https://example.com/a",
    )
    third = SourceDocument(
        id="3", source_type="notion", title="Different", text="Useful internal context"
    )
    result = deduplicate_documents([first, second, third])
    assert [item.id for item in result] == ["1", "3"]
