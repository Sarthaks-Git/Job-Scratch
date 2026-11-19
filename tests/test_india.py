from job_agent.india import check_preferred_location, is_india_job, location_matches, search_location


def test_india_job_detection():
    assert is_india_job("Bengaluru, Karnataka, India")
    assert is_india_job("KA, India")
    assert is_india_job("Gurgaon")
    assert is_india_job("Remote / Bengaluru, India / London, UK")  # multi-location incl. India
    assert not is_india_job("Indianapolis, Indiana, United States")
    assert not is_india_job("London, UK")
    assert not is_india_job("Remote")
    assert not is_india_job(None)


def test_india_native_sources_allow_wfh_and_blank():
    assert is_india_job("Work from home", "internshala")
    assert is_india_job(None, "naukri")
    assert not is_india_job("Dubai, UAE", "naukri")


def test_preferred_locations():
    assert check_preferred_location("Pune")[0]
    assert check_preferred_location("Remote")[0]
    assert not check_preferred_location("San Francisco, USA")[0]
    assert not check_preferred_location("Dubai")[0]
    ok, note = check_preferred_location("Tumkur")
    assert ok and "assuming" in note


def test_search_location():
    assert search_location("Pune") == "Pune, India"
    assert search_location("Remote") == "India"
    assert search_location("Delhi, India") == "Delhi, India"


def test_location_matches():
    assert location_matches("Bengaluru, India", ["Bangalore"], remote_ok=False)
    assert location_matches("Gurugram, Haryana", ["Delhi NCR"], remote_ok=False)
    assert not location_matches("Mysuru, Karnataka", ["Bengaluru"], remote_ok=False)
    assert location_matches("Work from home", ["Pune"], remote_ok=True)
    assert not location_matches("Work from home", ["Pune"], remote_ok=False)
    assert location_matches("Anywhere", [], remote_ok=False)
