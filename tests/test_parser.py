from src.parser.drain_parser import PAD_ID, UNK_ID, LogTemplateParser
from src.parser.loaders import parse_bgl, parse_hdfs, parse_thunderbird, parse_zookeeper

HDFS = "081109 203615 148 INFO dfs.DataNode$PacketResponder: PacketResponder 1 for block blk_38865049064139660 terminating"
BGL = "- 1117838570 2005.06.03 R02-M1-N0-C:J12-U11 2005-06-03-15-42-50.363779 R02-M1-N0-C:J12-U11 RAS KERNEL INFO instruction cache parity error corrected"
BGL_BAD = BGL.replace("- 1117838570", "KERNDTLB 1117838570", 1)
TBIRD = "- 1131566461 2005.11.09 dn228 Nov 9 12:01:01 dn228/dn228 crond(pam_unix)[2915]: session closed for user root"
ZK = "2015-07-29 19:04:12,394 - INFO  [/10.10.34.11:3888:QuorumCnxManager$Listener@493] - Received connection request /10.10.34.11:45307"


def test_loaders():
    h = parse_hdfs(HDFS)
    assert h.entity == "blk_38865049064139660" and h.component == "dfs.DataNode$PacketResponder"
    assert parse_bgl(BGL).label == 0 and parse_bgl(BGL_BAD).label == 1
    assert parse_bgl(BGL).component == "R02-M1"
    t = parse_thunderbird(TBIRD)
    assert t.component == "dn228" and t.content.startswith("crond")
    z = parse_zookeeper(ZK)
    assert z.component == "QuorumCnxManager$Listener" and "Received connection" in z.content
    assert parse_hdfs("garbage") is None


def test_drain_stable_ids():
    p = LogTemplateParser()
    a = p.parse("Received block blk_1 of size 100 from 10.0.0.1")
    b = p.parse("Received block blk_2 of size 999 from 10.0.0.2")
    c = p.parse("Failed to connect to namenode, retrying later")
    assert a == b and a != c and min(a, c) > UNK_ID > PAD_ID
    assert p.parse("Received block blk_3 of size 5 from 10.0.0.3", learn=False) == a
    assert p.parse("totally unseen kernel panic xyz abc def ghi", learn=False) == UNK_ID
    assert p.template_of(a) is not None and p.vocab_size == max(a, c) + 1


def test_save_load(tmp_path):
    p = LogTemplateParser()
    a = p.parse("hello world 1")
    p.save(str(tmp_path / "s.bin"))
    q = LogTemplateParser(load_state_from=str(tmp_path / "s.bin"))
    assert q.parse("hello world 2", learn=False) == a
