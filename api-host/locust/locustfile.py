from locust import HttpUser, task, between

class ApiUser(HttpUser):
    wait_time = between(0.5, 2)

    headers = {
        'X-Error-Rate': str(0.3),
        'X-Timeout-Prob': str(0.1),
        'X-Slow-DB-Prob': str(0.3),
        'X-Ext-API-Fail-Prob': str(0.05)
    }

    @task
    def get_data(self):
        self.client.get("/api/data", headers=self.headers)