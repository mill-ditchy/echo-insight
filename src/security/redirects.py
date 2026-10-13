"""Keep redirected media requests within their caller's allowed HTTPS domains."""

from urllib.request import HTTPRedirectHandler

from .network import validate_https_url


class DomainRedirect(HTTPRedirectHandler):
    def __init__(self, domains):
        super().__init__()
        self.domains = domains

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_https_url(newurl, self.domains)
        return super().redirect_request(req, fp, code, msg, headers, newurl)
