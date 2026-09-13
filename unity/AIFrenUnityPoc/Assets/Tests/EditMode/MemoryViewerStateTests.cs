using AIFren.UnityPoc.Protocol;
using AIFren.UnityPoc.UI;
using NUnit.Framework;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class MemoryViewerStateTests
    {
        [Test]
        public void ProtocolKeepsV1AuthorityDistinctFromNonAuthoritativeV2Episode()
        {
            ServerMessage message = AIFrenProtocol.ParseServerMessage(
                "{\"type\":\"event\",\"event\":{\"type\":\"memory_view_page\",\"data\":{" +
                "\"request_id\":\"request\",\"memory_page\":{\"character_id\":\"a\",\"lane\":\"episodes\"," +
                "\"authority_label\":\"Memory V2 episodes · derived, rebuildable, non-authoritative\"," +
                "\"items\":[{\"record_id\":\"episode\",\"lane\":\"episodes\"," +
                "\"authority\":\"Memory V2 derived episode · non-authoritative / rebuildable\"," +
                "\"content\":\"Synthetic episode\",\"derived\":true,\"editable\":false}]}}}}"
            );

            Assert.That(message.@event.data.memory_page.authority_label, Does.Contain("non-authoritative"));
            Assert.That(message.@event.data.memory_page.items[0].authority, Does.Contain("derived episode"));
            Assert.That(message.@event.data.memory_page.items[0].editable, Is.False);
            Assert.That(message.@event.data.memory_page.items[0].derived, Is.True);
        }

        [Test]
        public void ProtocolParsesBoundedProvenanceAndEpisodeDiagnostics()
        {
            ServerMessage message = AIFrenProtocol.ParseServerMessage(
                "{\"type\":\"event\",\"event\":{\"type\":\"memory_view_detail\",\"data\":{" +
                "\"request_id\":\"request\",\"memory_detail\":{\"character_id\":\"a\"," +
                "\"lane\":\"v2_claims\",\"record_id\":\"claim\",\"detail\":{" +
                "\"kind\":\"claim_provenance\",\"evidence\":[{\"event_id\":\"event\"," +
                "\"source_class\":\"explicit_viewer_correction\",\"sequence\":7}]," +
                "\"relations\":[{\"direction\":\"supersedes\",\"related_claim_id\":\"old\"}]}}}}}"
            );

            MemoryViewDetail detail = message.@event.data.memory_detail;
            Assert.That(detail.record_id, Is.EqualTo("claim"));
            Assert.That(detail.detail.evidence[0].source_class,
                Is.EqualTo("explicit_viewer_correction"));
            Assert.That(detail.detail.evidence[0].sequence, Is.EqualTo(7));
            Assert.That(detail.detail.relations[0].direction, Is.EqualTo("supersedes"));
        }

        [Test]
        public void CharacterSwitchInvalidatesSelectionAndRejectsStalePage()
        {
            MemoryViewerState state = new MemoryViewerState();
            state.ChangeCharacter("character-a");
            string requestA = state.BeginRequest();
            MemoryViewItem item = new MemoryViewItem { record_id = "v1:1", content = "A" };
            Assert.That(state.Accept(requestA, new MemoryViewPage { availability = "ready",
                character_id = "character-a", lane = "v1", items = new[] { item },
            }), Is.True);
            Assert.That(state.Select(item), Is.True);

            state.ChangeCharacter("character-b");

            Assert.That(state.Selected, Is.Null);
            Assert.That(state.Page, Is.Null);
            Assert.That(state.Accept(requestA, new MemoryViewPage { availability = "ready",
                character_id = "character-a", lane = "v1", items = new[] { item },
            }), Is.False);
            string requestB = state.BeginRequest();
            Assert.That(state.Accept(requestB, new MemoryViewPage { availability = "ready",
                character_id = "character-b", lane = "v1", items = new MemoryViewItem[0],
            }), Is.True);
        }

        [Test]
        public void CharacterSwitchInvalidatesAndRejectsStaleProvenanceDetail()
        {
            MemoryViewerState state = new MemoryViewerState();
            state.ChangeCharacter("character-a");
            state.CycleLane();
            string pageRequest = state.BeginRequest();
            MemoryViewItem item = new MemoryViewItem {
                record_id = "claim-a", lane = "v2_claims", content = "Synthetic claim",
            };
            Assert.That(state.Accept(pageRequest, new MemoryViewPage { availability = "ready",
                character_id = "character-a", lane = "v2_claims", items = new[] { item },
            }), Is.True);
            Assert.That(state.Select(item), Is.True);
            string detailRequest = state.BeginDetailRequest();

            state.ChangeCharacter("character-b");

            Assert.That(state.Selected, Is.Null);
            Assert.That(state.Detail, Is.Null);
            Assert.That(state.PendingDetailRequestId, Is.Empty);
            Assert.That(state.AcceptDetail(detailRequest, new MemoryViewDetail { availability = "ready",
                character_id = "character-a", lane = "v2_claims", record_id = "claim-a",
                detail = new MemoryViewDetailData { kind = "claim_provenance" },
            }), Is.False);
        }

        [Test]
        public void DetailMustMatchCurrentRequestLaneCharacterAndSelection()
        {
            MemoryViewerState state = new MemoryViewerState();
            state.ChangeCharacter("character-a");
            state.CycleLane();
            string pageRequest = state.BeginRequest();
            MemoryViewItem item = new MemoryViewItem {
                record_id = "claim-a", lane = "v2_claims", content = "Synthetic claim",
            };
            Assert.That(state.Accept(pageRequest, new MemoryViewPage { availability = "ready",
                character_id = "character-a", lane = "v2_claims", items = new[] { item },
            }), Is.True);
            Assert.That(state.Select(item), Is.True);
            string detailRequest = state.BeginDetailRequest();
            MemoryViewDetail detail = new MemoryViewDetail { availability = "ready",
                character_id = "character-a", lane = "v2_claims", record_id = "claim-a",
                detail = new MemoryViewDetailData {
                    kind = "claim_provenance",
                    evidence = new[] { new MemoryViewEvidence {
                        event_id = "event-a", source_class = "explicit_viewer_correction",
                    } },
                },
            };
            detail.detail.status_history = new MemoryViewStatusAudit[19];

            Assert.That(state.AcceptDetail("wrong-request", detail), Is.False);
            Assert.That(state.AcceptDetail(detailRequest, detail), Is.True);
            Assert.That(state.Detail.detail.evidence[0].source_class,
                Is.EqualTo("explicit_viewer_correction"));
            Assert.That(state.Detail.detail.status_history.Length,
                Is.EqualTo(MemoryViewerState.DetailSize));
            Assert.That(state.Detail.has_more, Is.True);
        }

        [Test]
        public void IncomingRowsAreDefensivelyBoundedAndFiltersResetPaging()
        {
            MemoryViewerState state = new MemoryViewerState();
            state.ChangeCharacter("character-a");
            string request = state.BeginRequest();
            MemoryViewItem[] items = new MemoryViewItem[27];
            for (int index = 0; index < items.Length; index++)
                items[index] = new MemoryViewItem { record_id = "v1:" + index, content = "Synthetic " + index };

            Assert.That(state.Accept(request, new MemoryViewPage { availability = "ready",
                character_id = "character-a", lane = "v1", items = items,
            }), Is.True);
            Assert.That(state.Page.items.Length, Is.EqualTo(MemoryViewerState.PageSize));
            Assert.That(state.Page.has_more, Is.True);
            Assert.That(state.NextPage(), Is.True);
            Assert.That(state.Offset, Is.EqualTo(MemoryViewerState.PageSize));

            state.CycleStatus();
            Assert.That(state.StatusFilter, Is.EqualTo("historical"));
            Assert.That(state.Offset, Is.Zero);
            state.CycleScope();
            Assert.That(state.ScopeFilter, Is.EqualTo("all"));
            state.CycleLane();
            Assert.That(state.Lane, Is.EqualTo("v2_claims"));
        }

        [Test]
        public void ItemLabelsShowLifecycleWithoutInternalAuthorityPromotion()
        {
            MemoryViewItem item = new MemoryViewItem {
                status = "superseded",
                content = "This is a deliberately long synthetic record used to verify bounded row labels remain readable in the viewer.",
            };
            string label = MemoryViewerState.ItemLabel(item);
            Assert.That(label, Does.StartWith("[superseded]"));
            Assert.That(label.Length, Is.LessThan(95));
        }
    }
}
