

def test_the_demo_load_source_matches_the_bridge_exactly():
    """The stand-in must not be more generous than the machine.

    DemoBridge returned a "volume" key S3kBridge did not, so the pane showed
    `vol 001` here and `vol 000` on hardware, and the tests agreed with the
    demo. The volume register turned out to exist after all (§96), so the key
    is back -- on both sides this time.

    Which side was wrong is a detail; that they disagreed is the defect, and
    it is why they are compared rather than each asserted alone.
    """
    import inspect
    from s3k import bridge as b
    from s3ked.demo import DemoBridge

    source = inspect.getsource(b.S3kBridge.load_source)
    real_keys = {
        line.split('"')[1]
        for line in source.splitlines()
        if line.strip().startswith('"') and '":' in line
    }
    assert real_keys, "could not read the bridge's own key list"
    assert set(DemoBridge().load_source()) == real_keys
