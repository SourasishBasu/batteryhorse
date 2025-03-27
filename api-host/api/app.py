import random
import time
import logging
import requests
from logging.handlers import RotatingFileHandler
from flask import Flask, request, jsonify
from werkzeug.exceptions import HTTPException
from prometheus_client import make_wsgi_app, Counter, Histogram, Gauge
from werkzeug.middleware.dispatcher import DispatcherMiddleware
from flask_cors import CORS, cross_origin

# Configuring structured logging
handler = RotatingFileHandler('/var/log/flask-app.log', maxBytes=1000000, backupCount=3)
handler.setFormatter(logging.Formatter(
    '{"time": "%(asctime)s", "level": "%(levelname)s", "request_id": "%(request_id)s", '
    '"endpoint": "%(endpoint)s", "message": "%(message)s", "duration": %(duration_ms)d}',
    defaults={
        'request_id': 'SYSTEM',  # Special value for non-request logs
        'endpoint': 'INTERNAL',
        'duration_ms': 0
    }
))

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
    'api_request_count',
    'Total API requests count',
    ['method', 'endpoint', 'http_status']
)
REQUEST_LATENCY = Histogram(
    'api_request_latency_seconds',
    'API request latency in seconds',
    ['method', 'endpoint']
)
ERROR_COUNT = Counter(
    'api_error_count',
    'Total API errors count',
    ['method', 'endpoint', 'error_type']
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

# Add Prometheus WSGI middleware
app.wsgi_app = DispatcherMiddleware(app.wsgi_app, {
    '/metrics': make_wsgi_app()
})

# Configuration for failure injection
FAILURE_CONFIG = {
    'error_rate': 0,          # 0-1 probability of random errors
    'slow_db_prob': 0,        # 0-1 probability of slow DB queries
    'ext_api_fail_prob': 0,   # 0-1 probability of external API failures
    'timeout_prob': 0         # 0-1 probability of timeouts
}

@app.before_request
def before_request():
    request.start_time = time.time()
    request.request_id = random.randint(1000, 9999)

@app.after_request
def after_request(response):
    # Calculate request duration
    duration = (time.time() - request.start_time) * 1000  # in ms
    
    # Log the request
    extra = {
        'request_id': request.request_id,
        'endpoint': request.path,
        'duration_ms': duration
    }
    logger.info(
        f"{request.method} {request.path} - {response.status_code}",
        extra=extra
    )
    
    # Record metrics
    REQUEST_COUNT.labels(
        request.method, request.path, response.status_code
    ).inc()
    REQUEST_LATENCY.labels(
        request.method, request.path
    ).observe(time.time() - request.start_time)
    
    return response

@app.errorhandler(Exception)
def handle_exception(e):
    # Log exceptions
    duration = (time.time() - request.start_time) * 1000
    extra = {
        'request_id': request.request_id,
        'endpoint': request.path,
        'duration_ms': duration
    }
    logger.error(
        f"Exception: {str(e)}",
        extra=extra
    )
    
    ERROR_COUNT.labels(
        request.method, request.path, e.__class__.__name__
    ).inc()
    
    if isinstance(e, HTTPException):
        response = jsonify(error=str(e.description))
        response.status_code = e.code
    else:
        response = jsonify(error="Internal server error")
        response.status_code = 500
    
    return response

def simulate_db_query():
    """Simulate a database query with potential failures"""

    # Random chance of slow query
    if random.random() < FAILURE_CONFIG['slow_db_prob']:
        delay = random.uniform(1, 5)  # 1-5 second delay
        time.sleep(delay)
        DB_QUERY_LATENCY.observe(delay)
        return {"data": "slow query result"}
    
    # Normal query
    delay = random.uniform(0.01, 0.1)
    time.sleep(delay)
    DB_QUERY_LATENCY.observe(delay)
    return {"data": "query result"}

def simulate_external_api_call():
    """Simulate calling an external API with potential failures"""

    if random.random() < FAILURE_CONFIG['timeout_prob']:
        time.sleep(3)  
        raise TimeoutError("External API timeout")
    
    # Random chance of failure
    if random.random() < FAILURE_CONFIG['ext_api_fail_prob']:
        raise ConnectionError("External API unavailable")
    
    # Normal call
    delay = random.uniform(0.05, 0.3)
    time.sleep(delay)
    EXTERNAL_API_LATENCY.observe(delay)
    return {"status": "success"}

@app.route('/api/health', methods = ['GET'])
def health():
    return jsonify({"status": "healthy"})


@app.route('/api/data', methods=['GET'])
def get_data():
    """Problematic endpoint with various potential failure modes"""
    error_rate = float(request.headers.get('X-Error-Rate', FAILURE_CONFIG['error_rate']))
    timeout_prob = float(request.headers.get('X-Timeout-Prob', FAILURE_CONFIG['timeout_prob']))
    slow_db_prob = float(request.headers.get('X-Slow-DB-Prob', FAILURE_CONFIG['slow_db_prob']))
    ext_api_fail_prob = float(request.headers.get('X-Ext-API-Fail-Prob', FAILURE_CONFIG['ext_api_fail_prob']))

    # Random chance of immediate error
    if random.random() < error_rate:
        raise ValueError("Random error occurred")
    
    # Simulate processing
    try:
        # DB Query with failures
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

        # External API with failures
        if random.random() < timeout_prob:
            time.sleep(3)
            raise TimeoutError("External API timeout")
        if random.random() < ext_api_fail_prob:
            raise ConnectionError("External API unavailable")
        
        # Simulate some processing
        time.sleep(random.uniform(0.01, 0.1))
        
        api_result = {"status": "success"}
        
        return jsonify({
            "status": "success",
            "db_data": db_result,
            "api_data": api_result
        })
    except Exception as e:
        raise e

@app.route('/simulate-requests', methods=['POST'])
def simulate_requests():
    """Simulate multiple requests with configured failures"""
    global FAILURE_CONFIG
    config = request.json.get('config', {})
    count = request.json.get('count', 10)
    
    # Run simulations
    results = []
    for _ in range(count):
        start_time = time.time()
        try:
            
            response = requests.get(
                f"{LOCAL_API_URL}/api/data",
                headers={
                    'X-Error-Rate': str(config.get('error_rate', 0)),
                    'X-Timeout-Prob': str(config.get('timeout_prob', 0)),
                    'X-Slow-DB-Prob': str(config.get('slow_db_prob', 0)),
                    'X-Ext-API-Fail-Prob': str(config.get('ext_api_fail_prob', 0))
                },
                timeout=3
            )
            results.append({
                "status": "success",
                "code": response.status_code,
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
        except requests.exceptions.ConnectionError:
            results.append({
                "status": "error",
                "code": 503,
                "duration": time.time() - start_time,
                "error": "Connection failed"
            })
        except Exception as e:
            results.append({
                "status": "error",
                "code": 500,
                "duration": time.time() - start_time,
                "error": str(e)
            })
    return jsonify({
        "config": config,
        "results": results,
        "summary": {
            "success_count": sum(1 for r in results if r['status'] == 'success'),
            "error_count": sum(1 for r in results if r['status'] == 'error'),
            "avg_duration": sum(r['duration'] for r in results) / count
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