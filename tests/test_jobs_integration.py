def test_full_job_lifecycle(client):
    create_resp = client.post("/v1/jobs", json={"job_type": "sft", "config": {"lr": 0.001}})
    assert create_resp.status_code == 201
    body = create_resp.json()
    job_id = body["id"]
    assert body["status"] == "QUEUED"  # V0.2: job auto-queues on creation
    assert body["job_type"] == "sft"

    get_resp = client.get(f"/v1/jobs/{job_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == job_id

    list_resp = client.get("/v1/jobs")
    assert list_resp.status_code == 200
    list_body = list_resp.json()
    assert list_body["total"] == 1
    assert list_body["items"][0]["id"] == job_id

    patch_resp = client.patch(f"/v1/jobs/{job_id}", json={"status": "CANCELLED"})
    assert patch_resp.status_code == 200
    assert patch_resp.json()["status"] == "CANCELLED"

    delete_resp = client.delete(f"/v1/jobs/{job_id}")
    assert delete_resp.status_code == 204

    get_after_delete = client.get(f"/v1/jobs/{job_id}")
    assert get_after_delete.status_code == 404

    list_after_delete = client.get("/v1/jobs")
    assert list_after_delete.json()["total"] == 0


def test_get_nonexistent_job_returns_404(client):
    resp = client.get("/v1/jobs/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


def test_pagination(client):
    for i in range(5):
        client.post("/v1/jobs", json={"job_type": f"type-{i}"})

    first_page = client.get("/v1/jobs?limit=2&offset=0")
    assert len(first_page.json()["items"]) == 2
    assert first_page.json()["total"] == 5

    last_page = client.get("/v1/jobs?limit=2&offset=4")
    assert len(last_page.json()["items"]) == 1
