from flask import Flask, request, jsonify
from flask_cors import CORS, cross_origin
import google.generativeai as genai
import os
from datetime import datetime
import requests
import time

app = Flask(__name__)
genai.configure(api_key=os.getenv('GEMINI_API_KEY'))
model = genai.GenerativeModel('gemini-2.0-flash')

CORS(app, resources={r"/*": {"origins": "*",}})

FAILURE_CONFIG = {
    'error_rate': 0,          # 0-1 probability of random errors
    'slow_db_prob': 0,        # 0-1 probability of slow DB queries
    'ext_api_fail_prob': 0,   # 0-1 probability of external API failures
    'timeout_prob': 0         # 0-1 probability of timeouts
}

@app.route('/ai-analyze', methods=['POST'])
def ai_analyze():
    logs = request.json.get('logs')
    metrics = request.json.get('metrics')
    
    prompt = f"""
    Analyze these application logs and metrics:
    {logs}
    {metrics}
    
    Provide:
    1. Error classification
    2. Probable root cause
    3. Recommended actions
    """
    
    response = model.generate_content(prompt)
    return jsonify({"analysis": response.text})

@app.route('/analyze', methods=['POST'])
def analyze():
    timestamp = request.json.get('timestamp', datetime.now().isoformat())
    
    # Mock analysis report
    report = f"""SYSTEM ANALYSIS REPORT ({timestamp})
    
=== Metrics Snapshot ===
• API Error Rate: 12.5%
• Avg Response Time: 342ms
• CPU Usage: 68%
• Memory Usage: 45%

=== Top Errors ===
1. DatabaseTimeout (43%) - Check DB connection pool
2. ExternalAPIFailure (32%) - Service X is responding slowly
3. ValidationError (25%) - Invalid user input

=== Recommendations ===
1. Increase DB connection pool size
2. Add retries for Service X API calls
3. Validate inputs before processing"""

    return jsonify({
        "status": "success",
        "timestamp": timestamp,
        "report": report
    })

@app.route('/api/health', methods = ['GET'])
def health():
    return jsonify({"status": "healthy"})

@app.route('/simulate-requests', methods=['POST'])
def simulate_requests():
    """Simulate multiple requests with configured failures"""
    
    config = request.json.get('config', {})
    count = request.json.get('count', 10)
    
    # Configure failures
    for key in FAILURE_CONFIG:
        if key in config:
            FAILURE_CONFIG[key] = config[key]
    
    # Run simulations
    results = []
    for _ in range(count):
        start_time = time.time()
        try:
            response = requests.get(
                f"http://209.38.123.115:5000/api/data"
            )
            results.append({
                "status": "success",
                "code": 200,
                "duration": time.time() - start_time,
                "data": response.json()
            })
        except requests.exceptions.Timeout:
            results.append({
                "status": "error",
                "code": 504,
                "duration": time.time() - start_time,
                "error": "Request timeout"
            })
        except requests.exceptions.RequestException as e:
            results.append({
                "status": "error",
                "code": 500,
                "duration": time.time() - start_time,
                "error": str(e)
            })
        except Exception as e:
            results.append({
                "status": "error",
                "code": 500,
                "duration": time.time() - start_time,
                "error": str(e)
            })
    return jsonify({
        "config": FAILURE_CONFIG,
        "results": results,
        "summary": {
            "success_count": sum(1 for r in results if r['status'] == 'success'),
            "error_count": sum(1 for r in results if r['status'] == 'error'),
            "avg_duration": sum(r['duration'] for r in results) / count
        }
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)