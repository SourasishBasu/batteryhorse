from locust import HttpUser, task, between

class ApiUser(HttpUser):
    wait_time = between(0.5, 2)

    @task
    def get_data(self):
        self.client.get("/api/data")

    @task(3)
    def trigger_errors(self):
        # Configure random failures
        self.client.post("/configure-failures", json={
            "error_rate": 0.2,
            "slow_db_prob": 0.3,
            "ext_api_fail_prob": 0.2,
            "timeout_prob": 0.1
        })