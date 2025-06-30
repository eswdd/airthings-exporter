import requests as requests
import time
from prometheus_client.metrics_core import GaugeMetricFamily
from prometheus_client.registry import Collector
from prometheus_client import Counter, Gauge



class CloudCollector(Collector):
    def __init__(self, client_id, client_secret, device_id_list):
        self.client_id = client_id
        self.client_secret = client_secret
        self.device_id_list = device_id_list
        self.rate_limit_remaining = Gauge(
            'airthings_api_rate_limit_remaining',
            'Number of requests remaining in the Airthings API rate limit'
        )
        self.rate_limit_resets = Gauge(
            'airthings_api_rate_limit_reset_time',
            'Time when the Airthings API rate limit will reset (in seconds since epoch)'
        )
        self.data_requests_counter = Counter(
            'airthings_api_data_requests',
            'Total number of requests made to Airthings API',
            ['device_id']
        )
        self.data_requests_error_counter = Counter(
            'airthings_api_data_requests_errors',
            'Total number of requests made to Airthings API that resulted in an error',
            ['device_id','error']
        )
        self.token_requests_counter = Counter(
            'airthings_api_token_requests',
            'Total number of requests made to Airthings API'
        )
        self.token_requests_error_counter = Counter(
            'airthings_api_token_requests_errors',
            'Total number of requests made to Airthings API that resulted in an error',
            ['error']
        )
        self.access_token = None
        self.access_token_expiry = None
        self.rate_limit_reset = 0

    def collect(self):
        gauge_metric_family = GaugeMetricFamily('airthings_gauge', 'Airthings sensor values')
        self.__get_access_token__()
        for device_id in self.device_id_list:
            data = self.__get_cloud_data__(device_id)
            if data is None:
                continue
            self.__add_samples__(gauge_metric_family, data, device_id)
        yield gauge_metric_family

    def __add_samples__(self, gauge_metric_family, data, device_id):
        labels = {'device_id': device_id}
        if 'battery' in data:
            gauge_metric_family.add_sample('airthings_battery_percent', value=data['battery'], labels=labels)
        if 'co2' in data:
            gauge_metric_family.add_sample('airthings_co2_parts_per_million', value=data['co2'], labels=labels)
        if 'humidity' in data:
            gauge_metric_family.add_sample('airthings_humidity_percent', value=data['humidity'], labels=labels)
        if 'pm1' in data:
            gauge_metric_family.add_sample('airthings_pm1_micrograms_per_cubic_meter',
                                           value=float(data['pm1']),
                                           labels=labels)
        if 'pm25' in data:
            gauge_metric_family.add_sample('airthings_pm25_micrograms_per_cubic_meter',
                                           value=float(data['pm25']),
                                           labels=labels)
        if 'pressure' in data:
            gauge_metric_family.add_sample('airthings_pressure_hectopascals',
                                           value=float(data['pressure']),
                                           labels=labels)
        if 'radonShortTermAvg' in data:
            gauge_metric_family.add_sample('airthings_radon_short_term_average_becquerels_per_cubic_meter',
                                           value=float(data['radonShortTermAvg']),
                                           labels=labels)
        if 'temp' in data:
            gauge_metric_family.add_sample('airthings_temperature_celsius', value=data['temp'], labels=labels)
        if 'voc' in data:
            gauge_metric_family.add_sample('airthings_voc_parts_per_billion', value=data['voc'], labels=labels)

    def __get_cloud_data__(self, device_id):
        if self.rate_limit_reset > time.time():
            wait_time = self.rate_limit_reset - time.time()
            print(f"Rate limit exceeded for device {device_id}. Waiting {wait_time} secs until reset at {self.rate_limit_reset}.")
            return None

        self.data_requests_counter.labels(device_id=device_id).inc()
        headers = {'Authorization': f'Bearer {self.access_token}'}
        response = requests.get(
            f'https://ext-api.airthings.com/v1/devices/{device_id}/latest-samples',
            headers=headers)
        
        rate_limit_remaining = int(response.headers.get('X-RateLimit-Remaining', '0'))
        self.rate_limit_remaining.set(rate_limit_remaining)

        json = response.json()

        rate_limit_reset_value = int(response.headers['X-RateLimit-Reset'])
        self.rate_limit_resets.set(rate_limit_reset_value)

        if 'data' in json:
            return json['data']
        
        if 'error' in json:
            self.data_requests_error_counter.labels(device_id=device_id, error=json['error']).inc()
            if json['error'] == 'INVALID_REQUEST_CLIENTS_LIMIT_EXCEEDED':
                # 'headers': {'Content-Type': 'application/json', 'Content-Length': '117', 'Connection': 'keep-alive', 'Date': 'Sun, 29 Jun 2025 21:34:13 GMT', 'X-Request-ID': 'c153dbdd-d12a-4335-87ae-777dc093889e', 'X-RateLimit-Limit': '120', 'X-RateLimit-Remaining': '0', 'X-RateLimit-Reset': '1751236440', 'X-RateLimit-Retry-After': '0', 'Cache-Control': 'max-age=30', 'X-Cache': 'Error from cloudfront', 'Via': '1.1 ad6a59dd9fdc1afb57f7131fcd96bf20.cloudfront.net (CloudFront)', 'X-Amz-Cf-Pop': 'LHR50-P3', 'X-Amz-Cf-Id': 'wHl83J5zGSu9jiF4AcQLCfXvBVO3e3qDmZyf7mTQArC-uwjfaOh0fA==', 'X-XSS-Protection': '1; mode=block', 'X-Frame-Options': 'SAMEORIGIN', 'Referrer-Policy': 'strict-origin-when-cross-origin', 'X-Content-Type-Options': 'nosniff', 'Strict-Transport-Security': 'max-age=31536000', 'Vary': 'Origin'}
                print(f"Rate limit exceeded for device {device_id}.")
                self.rate_limit_reset = rate_limit_reset_value
                return None
            
        print(f"Response for device {device_id} did not contain 'data': {json}\n'headers': {response.headers}")
        return None

    def __get_access_token__(self):
        if self.access_token is not None and self.access_token_expiry is not None and self.access_token_expiry > time.time():
            return

        self.token_requests_counter.inc()
        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": "read:device:current_values"
        }
        token_response = requests.post(
            'https://accounts-api.airthings.com/v1/token',
            data=data)
        json = token_response.json()
        #print(f"Response from token endpoint: {json}")
        if 'error' in json:
            self.token_requests_error_counter.labels(error=json['error']).inc()
        if 'access_token' not in json:
            raise Exception(f"Failed to get access token: {json}")
        
        # Store the access token and its expiry time
        self.access_token = json['access_token']
        # Set the expiry time to 60 seconds before the actual expiry to allow for clock skew/lag
        self.access_token_expiry = time.time() + json.get('expires_in', 10800) - 60
