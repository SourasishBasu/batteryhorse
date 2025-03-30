import os
import time
import random
import logging
import requests
from logging.handlers import RotatingFileHandler
from flask import Flask, request, jsonify
from werkzeug.exceptions import HTTPException
from prometheus_client import make_wsgi_app, Counter, Histogram
from werkzeug.middleware.dispatcher import DispatcherMiddleware
from flask_cors import CORS

os.environ['TZ'] = 'UTC'
time.tzset()

# Configuring structured logging
handler = RotatingFileHandler('/var/log/flask-app.log', maxBytes=1000000, backupCount=2)
handler.setFormatter(logging.Formatter(
    '{"time": "%(asctime)s", "level": "%(levelname)s", "request_id": "%(request_id)s", '
    '"endpoint": "%(endpoint)s", "message": "%(message)s", "duration": %(duration_ms)d, '
    '"http_status": "%(http_status)s", "error_type": "%(error_type)s", "severity": "%(severity)s"}',
    defaults={
        'request_id': 'SYSTEM',
        'endpoint': 'INTERNAL',
        'duration_ms': 0,
        'http_status': '000',
        'error_type': 'none',
        'severity': 'none'
    }
))

class CustomError(Exception):
    """Enhanced error class with classification for error severity and types"""
    def __init__(self, error_type, message, severity, http_status):
        self.error_type = error_type
        self.message = message
        self.severity = severity
        self.http_status = http_status
        super().__init__(message)

class MetricsSafeFilter(logging.Filter):
    def filter(self, record):
        if not hasattr(record, 'http_status'):
            record.http_status = '000'  # Default for non-request logs
        return True
    
logger = logging.getLogger()
logger.addFilter(MetricsSafeFilter())
logger.setLevel(logging.INFO)
logger.addHandler(handler)

LOCAL_API_URL = "http://localhost:5000" 

# Prometheus metrics
REQUEST_COUNT = Counter(
    'api_request_total',
    'Total API requests count',
    ['method', 'endpoint', 'http_status', 'status_class']
)
REQUEST_LATENCY = Histogram(
    'api_request_duration_seconds',
    'API request duration in seconds',
    ['method', 'endpoint', 'http_status']
)
ERROR_COUNT = Counter(
    'api_error_count',
    'Total API errors count',
    ['method', 'endpoint', 'error_type', 'http_status', 'severity']
)
DB_QUERY_LATENCY = Histogram(
    'db_query_latency_seconds',
    'Database query latency in seconds'
)
EXTERNAL_API_LATENCY = Histogram(
    'external_api_latency_seconds',
    'External API call latency in seconds'
)

app = Flask(__name__)

CORS(app, resources={r"/*": {"origins": "*",}})

app.wsgi_app = DispatcherMiddleware(app.wsgi_app, {
    '/metrics': make_wsgi_app()
})

# default
FAILURE_CONFIG = {
    'error_rate': 0,          
    'slow_db_prob': 0,        
    'ext_api_fail_prob': 0,   
    'timeout_prob': 0        
}

@app.before_request
def before_request():
    request.start_time = time.time()
    request.request_id = random.randint(1000, 9999)

@app.after_request
def after_request(response):
    """
    Updates Prometheus request related metrics data and logs after request finishes
    """
    duration = (time.time() - request.start_time) * 1000
    status_class = f"{response.status_code // 100}xx"    
    extra = {
        'request_id': request.request_id,
        'endpoint': request.path,
        'duration_ms': duration
    }
    logger.info(
        f"{request.method} {request.path} - {response.status_code}",
        extra=extra
    )
    
    REQUEST_COUNT.labels(
        request.method, request.path, response.status_code, status_class
    ).inc()
    REQUEST_LATENCY.labels(
        request.method, request.path, response.status_code
    ).observe(time.time() - request.start_time)
    
    return response

@app.errorhandler(Exception)
def handle_exception(e):
    """
    Updates Prometheus error related metrics data after exception handling
    """
    duration = (time.time() - request.start_time) * 1000
    extra = {
        'request_id': request.request_id,
        'endpoint': request.path,
        'duration_ms': duration,
        'http_status': getattr(e, 'http_status', 500),
        'error_type': getattr(e, 'error_type', e.__class__.__name__),
        'severity': getattr(e, 'severity', 'critical')
    }
    logger.error(f"Exception: {str(e)}", extra=extra)
    
    ERROR_COUNT.labels(
        request.method, 
        request.path, 
        extra['error_type'],
        extra['http_status'],
        extra['severity']
    ).inc()
    
    response = jsonify({
        "error": str(e),
        "type": extra['error_type'],
        "severity": extra['severity']
    })
    response.status_code = extra['http_status']
    return response

@app.route('/api/health', methods = ['GET'])
def health():
    """
    Health check route for API
    """
    return jsonify({"status": "healthy"})


@app.route('/api/data', methods=['GET'])
def get_data():
    """Endpoint for classified failures"""
    error_rate = float(request.headers.get('X-Error-Rate', FAILURE_CONFIG['error_rate']))
    timeout_prob = float(request.headers.get('X-Timeout-Prob', FAILURE_CONFIG['timeout_prob']))
    slow_db_prob = float(request.headers.get('X-Slow-DB-Prob', FAILURE_CONFIG['slow_db_prob']))
    ext_api_fail_prob = float(request.headers.get('X-Ext-API-Fail-Prob', FAILURE_CONFIG['ext_api_fail_prob']))

    # Error simulation
    if random.random() < error_rate:

        error_type = random.choice([
            # Mild errors
            ("RateLimitExceeded", "API rate limit exceeded", "mild", 429),
            ("ValidationError", "Invalid input parameters", "mild", 400),
            ("DatabaseLock", "Temporary database lock", "mild", 504),

            # Critical errors
            ("DatabaseConnectionError", "Database unreachable", "critical", 503),
            ("InternalServerError", "Critical system failure", "critical", 500),

            # External errors
            ("ForbiddenAccess", "Third-party auth failure", "external", 403)
        ])
        
        raise CustomError(*error_type)
    
    try:
        # Delays from 'slow DB queries' (performance degradation)
        if random.random() < slow_db_prob:
            delay = random.uniform(1, 5)
            time.sleep(delay)
            DB_QUERY_LATENCY.observe(delay)
            db_result = {"data": "slow query result"}
        else:
            delay = random.uniform(0.01, 0.1)
            time.sleep(delay)
            DB_QUERY_LATENCY.observe(delay)
            db_result = {"data": "query result"}

        # External API failures (downstream issue)
        if random.random() < timeout_prob:
            raise CustomError(
                "ExternalTimeout", 
                "Downstream service timeout", 
                "external",
                504
            )
        
        if random.random() < ext_api_fail_prob:
            raise CustomError(
                "ExternalServiceError",
                "Payment service unavailable",
                "external",
                503
            )

        api_result = {"data": "api result"}

        return jsonify({"db_data": db_result, "api_data": api_result})
        
    except Exception as e:
        if isinstance(e, CustomError):
            raise e
        raise CustomError(
            "SystemError",
            f"Unexpected system failure: {str(e)}",
            "critical",
            500
        ) from e

@app.route('/simulate-requests', methods=['POST'])
def simulate_requests():
    """Route to trigger simulation of API errors"""
    config = request.json.get('config', {})
    count = request.json.get('count', 10)
    
    results = []
    error_counts = {
        "mild": 0,
        "critical": 0,
        "external": 0
    }
    total_duration=0
    headers = {
        'X-Error-Rate': str(config.get('error_rate', 0.3)),
        'X-Timeout-Prob': str(config.get('timeout_prob', 0.1)),
        'X-Slow-DB-Prob': str(config.get('slow_db_prob', 0.2)),
        'X-Ext-API-Fail-Prob': str(config.get('ext_api_fail_prob', 0.1))
    }
    for _ in range(count):
        start_time = time.time()
        try:
            response = requests.get(f"{LOCAL_API_URL}/api/data", 
                                 headers=headers, timeout=3)
            duration = time.time() - start_time
            total_duration += duration

            if response.status_code >= 400:
                error_data = {
                    "status": "error",
                    "code": response.status_code,
                    "error": response.json().get('error', 'Unknown error'),
                    "type": response.json().get('type', 'unknown'),
                    "severity": response.json().get('severity', 'critical'),
                    "duration": duration
                }
                results.append(error_data)
                error_counts[error_data["severity"]] += 1
            else:
                results.append({
                    "status": "success",
                    "code": response.status_code,
                    "data": response.json(),
                    "duration": duration
                })
        except requests.exceptions.RequestException as e:
            duration = time.time() - start_time
            total_duration += duration
            error_data = {
                "status": "error",
                "code": 500,
                "error": str(e),
                "type": "RequestException",
                "severity": "critical",
                "duration": duration
            }
            results.append(error_data)
            error_counts["critical"] += 1

    return jsonify({
        "config": config,
        "results": results,
        "summary": {
            "total_requests": count,
            "success_count": len([r for r in results if r['status'] == 'success']),
            **error_counts,
            "avg_duration": total_duration / count if count > 0 else 0,
        }
    })

@app.route('/configure-failures', methods=['POST'])
def configure_failures():
    """Endpoint to configure failure injection"""
    global FAILURE_CONFIG
    config = request.get_json()
    for key in FAILURE_CONFIG:
        if key in config:
            FAILURE_CONFIG[key] = config[key]
    return jsonify(FAILURE_CONFIG)
        
            
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)