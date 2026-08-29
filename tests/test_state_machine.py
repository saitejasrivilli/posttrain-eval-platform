from app.services import jobs as service
from app.schemas import JobCreate


def test_invalid_transition_returns_409(client, db_session):
    job = service.create_job(db_session, JobCreate(job_type="sft"))
    # QUEUED -> SUCCEEDED skips RUNNING entirely: illegal.
    resp = client.patch(f"/v1/jobs/{job.id}", json={"status": "SUCCEEDED"})
    assert resp.status_code == 409
    assert resp.json()["detail"]["from"] == "QUEUED"
    assert resp.json()["detail"]["to"] == "SUCCEEDED"


def test_terminal_state_rejects_any_further_transition(client, db_session):
    job = service.create_job(db_session, JobCreate(job_type="sft"))
    cancel_resp = client.patch(f"/v1/jobs/{job.id}", json={"status": "CANCELLED"})
    assert cancel_resp.status_code == 200

    # CANCELLED -> RUNNING must be rejected; the classic "SUCCEEDED -> RUNNING"
    # style illegal resurrection the design review called out.
    resurrect_resp = client.patch(f"/v1/jobs/{job.id}", json={"status": "RUNNING"})
    assert resurrect_resp.status_code == 409


def test_job_auto_queues_on_creation(db_session):
    job = service.create_job(db_session, JobCreate(job_type="sft"))
    assert job.status == "QUEUED"
