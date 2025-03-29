from flask import Flask, request, jsonify
from flask_cors import CORS
import google.generativeai as genai
import os
from typing import Dict, Any
from datetime import datetime
import requests
import time
import json

genai.configure(api_key=os.getenv('GEMINI_API_KEY', 'AIzaSyBf5xDMXWPEFiKf2C44DyoDXACvevPY3DU'))
model = genai.GenerativeModel('gemini-2.0-flash')

app = Flask(__name__)

CORS(app, resources={r"/*": {"origins": "*",}})

class MetricsAnalyzer:
    def __init__(self):
        self.PROMETHEUS_URL = os.environ.get(
            'PROMETHEUS_URL', 
            'http://prometheus:9090'
        )
    
    def fetch_prometheus_metrics(self, time_range_minutes: int = 20) -> Dict[str, Any]:
        """
        Fetch metrics from Prometheus for the last 20 minutes
        """
        end_time = time.time()
        start_time = end_time - (time_range_minutes * 60)
        
        # Metrics Queries
        queries = {
            'error_rate': f'sum(rate(api_request_total{{endpoint="/api/data", http_status=~"[45].."}}[{time_range_minutes}m])) / sum(rate(api_request_total[{time_range_minutes}m])) * 100',
            'avg_response_time': f'histogram_quantile(0.95, sum(rate(api_request_duration_seconds_bucket{{endpoint="/api/data"}}[{time_range_minutes}m])) by (le))',
            'mild_error_rate': f'sum(rate(api_error_count_total{{endpoint="/api/data", severity="mild"}}[{time_range_minutes}m])) / sum(rate(api_request_total{{endpoint="/api/data"}}[{time_range_minutes}m])) * 100',
            'external_error_rate': f'sum(rate(api_error_count_total{{endpoint="/api/data", severity="external"}}[{time_range_minutes}m])) / sum(rate(api_request_total{{endpoint="/api/data"}}[{time_range_minutes}m])) * 100',
            'critical_error_rate': f'sum(rate(api_error_count_total{{endpoint="/api/data", severity="critical"}}[{time_range_minutes}m])) / sum(rate(api_request_total{{endpoint="/api/data"}}[{time_range_minutes}m])) * 100',
            'top_errors': 'topk(4, sum(api_error_count_total) by (error_type, severity))',
            'cpu_usage': f'100 - (avg by (instance) (rate(node_cpu_seconds_total{{mode="idle"}}[{time_range_minutes}m])) * 100)',
            'memory_usage': '(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100'
        }
        
        metrics_data = {}
        for metric_name, query in queries.items():
            response = requests.get(f'{self.PROMETHEUS_URL}/api/v1/query', 
                                    params={'query': query})
            if response.status_code == 200:
                metrics_data[metric_name] = response.json()['data']['result']
        
        return metrics_data
    
    def process_metrics(self, metrics: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process raw Prometheus metrics into structured format
        """
        processed = {
            'error_rate': 0,
            'avg_response_time': 0,
            'cpu_usage': 0,
            'memory_usage': 0,
            'errors_by_severity': {},
            'top_errors': []
        }
        
        # Extract error rate
        if metrics.get('error_rate'):
            processed['error_rate'] = float(metrics['error_rate'][0]['value'][1])
        
        # Extract average response time (convert to ms)
        if metrics.get('avg_response_time'):
            processed['avg_response_time'] = float(metrics['avg_response_time'][0]['value'][1])
        
        # Extract CPU and Memory Usage
        if metrics.get('cpu_usage'):
            processed['cpu_usage'] = float(metrics['cpu_usage'][0]['value'][1])
        
        if metrics.get('memory_usage'):
            processed['memory_usage'] = float(metrics['memory_usage'][0]['value'][1])
        
        # Process errors by severity
        if metrics.get('mild_error_rate') and metrics.get('external_error_rate') and metrics.get('critical_error_rate'):
            severity = ["mild", "critical", "external"]
            for item in severity:
                value = metrics.get(f'{item}_error_rate', 'unknown')[0]['value'][1]
                processed['errors_by_severity'][item.title()] = float(value)
        
        # Process top errors
        if metrics.get('top_errors'):
            for item in metrics['top_errors']:
                processed['top_errors'].append({
                    'error_type': item['metric'].get('error_type', 'Unknown'),
                    'severity': item['metric'].get('severity', 'Unknown'),
                    'count': int(item['value'][1])
                })
        
        return processed
    
    def generate_ai_analysis(self, metrics: Dict[str, Any]) -> str:
        """
        Generate AI-powered error analysis using Gemini
        """

        pretty_metrics = f"""
Analysis of latest API metrics

=== Metrics Snapshot ===
- API Error Rate: {metrics['error_rate']:.2f}%
- Avg Response Time: {metrics['avg_response_time']:.2f}ms
- CPU Usage: {metrics['cpu_usage']:.2f}%
- Memory Usage: {metrics['memory_usage']:.2f}%

=== Top Errors Overall ===
{json.dumps(metrics['top_errors'], indent=2)}

=== Error Rates by Severity over past 20 minutes ===
- Mild Errors: {metrics['errors_by_severity']['Mild']:.2f}%
- Critical Errors: {metrics['errors_by_severity']['Critical']:.2f}%
- External Errors: {metrics['errors_by_severity']['External']:.2f}%
        """

        prompt = f"""
You are provided metrics related to an API's various performance stats along with it's host's system stats for a period of 30 minutes. Analyze them and provide insights:

=== Metrics Snapshot ===
- API Error Rate: {metrics['error_rate']:.2f}%
- Avg Response Time: {metrics['avg_response_time']:.2f}ms
- CPU Usage: {metrics['cpu_usage']:.2f}%
- Memory Usage: {metrics['memory_usage']:.2f}%

=== Top Errors ===
{json.dumps(metrics['top_errors'], indent=2)}

=== Errors Rates by Severity over past 20 minutes ===
- Mild Errors: {metrics['errors_by_severity']['Mild']:.2f}%
- Critical Errors: {metrics['errors_by_severity']['Critical']:.2f}%
- External Errors: {metrics['errors_by_severity']['External']:.2f}%

Guidelines for analysis:
- Mild errors: Note but don't over-emphasize since these don't require immediate attention
- Critical errors: Require immediate attention
- External errors: Downstream issues, likely do not relate to API itself
- CPU and memory stats: If unusually high, factor those into the analysis as well in relation with error messages
- The input is from a simulated API made to have a high error rate, so DO NOT mention anything about error rate.

Given the top overall errors, error rates over the past 20 minute duration, provide a professional, technically precise error analysis that covers 3 sections:
1. Key observations
2. Potential root causes in short without merely restating error messages
3. High level, specific, actionable recommendations 

Constraints:
- Use clear, professional language
- Focus on systemic patterns, not individual error instances
- Be technically precise
- Avoid hypothetical scenarios or speculative solutions
- Limit response to 3-4 sentences.
        """
        
        response = model.generate_content(prompt)

        return {
        "metrics": {
            "error_rate": round(metrics['error_rate'], 2),
            "response_time": round(metrics['avg_response_time'], 2),
            "cpu_usage": round(metrics['cpu_usage'], 2),
            "memory_usage": round(metrics['memory_usage'], 2),
            "severity_rates": {
                "mild": round(float(metrics['errors_by_severity']['Mild']), 2),
                "critical": round(float(metrics['errors_by_severity']['Critical']), 2),
                "external": round(float(metrics['errors_by_severity']['External']), 2)
            },
            "top_errors": metrics['top_errors']
        },
        "analysis": response.text.split('\n')
    }
        
        #return response.text, pretty_metrics

metrics_analyzer = MetricsAnalyzer()

@app.route('/api/health', methods = ['GET'])
def health():
    return jsonify({"status": "healthy"})

@app.route('/analyze', methods=['GET'])
def get_metrics_analysis():
    try:
        raw_metrics = metrics_analyzer.fetch_prometheus_metrics()
        
        processed_metrics = metrics_analyzer.process_metrics(raw_metrics)
        
        ai_analysis = metrics_analyzer.generate_ai_analysis(processed_metrics)
        
        return jsonify({
            "status": "success",
            'report': ai_analysis
        })
    
    except Exception as e:
        return jsonify({
            'status': 'error',
            'report': str(e),
        }), 500
    
@app.route('/mock-analyze', methods=['POST'])
def get_mock_analysis():
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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)