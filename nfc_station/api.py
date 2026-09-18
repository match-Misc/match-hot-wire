import json
import urllib.error
import urllib.request


class APIError(Exception):
    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class DashboardAPI:
    def __init__(self, url, key):
        self.url, self.key = url.rstrip('/'), key

    def request(self, path, payload=None):
        request = urllib.request.Request(self.url + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={'X-API-Key': self.key, 'Content-Type': 'application/json'})
        # Event LAN requests must not leave via an inherited corporate HTTP proxy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try:
            with opener.open(request, timeout=4) as response:
                if response.geturl() != self.url + path:
                    raise APIError('Unerwartete Weiterleitung vom Dashboard')
                return json.load(response)
        except urllib.error.HTTPError as exc:
            messages = {401: 'Stationsschlüssel ungültig', 404: 'Tag nicht registriert',
                        409: 'Ergebniskonflikt', 422: 'Ergebnis vom Server abgelehnt'}
            raise APIError(messages.get(exc.code, f'Dashboard antwortet mit HTTP {exc.code}'),
                           exc.code == 429 or exc.code >= 500) from exc
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise APIError('Dashboard momentan nicht erreichbar', retryable=True) from exc

    def player(self, uid):
        player = self.request('/api/tags/' + uid)
        if (not isinstance(player, dict) or type(player.get('id')) is not int or player['id'] < 1
                or not isinstance(player.get('name'), str) or not player['name'].strip()):
            raise APIError('Ungültige Spielerantwort')
        return {'id': player['id'], 'name': player['name']}

    def submit(self, payload):
        games = self.request('/api/games')
        if not any(g.get('id') == payload['game'] and payload['difficulty'] in g.get('difficulties', [])
                   for g in games.get('games', [])):
            raise APIError('Schwierigkeit ist am Dashboard nicht eingerichtet')
        result = self.request('/api/results', payload)
        if (not isinstance(result, dict) or type(result.get('id')) is not int
                or type(result.get('created')) is not bool or type(result.get('removed')) is not bool):
            # The POST may already be committed. Retrying its UUID is safe.
            raise APIError('Unvollständige Ergebnisbestätigung', retryable=True)
        if result['removed']:
            raise APIError('Dieses Ergebnis wurde am Dashboard entfernt')
        return result
