from core import app, request_context, theta_portfolio


def test_review_route_rejects_missing_guest_and_wrong_owner(monkeypatch):
    monkeypatch.setenv('CIPHER_THETA_REVIEW_USER_ID', 'owner')
    for context in [None, request_context.ProviderRequestContext('guest', None, None, True), request_context.ProviderRequestContext('other', None, None)]:
        request_context.clear()
        if context:
            request_context.activate(context)
        handler = object.__new__(app.Handler)
        handler.path = '/api/theta-review'
        responses = []
        handler.send_json = lambda status, body: responses.append((status, body))
        handler._read_json_body = lambda: (_ for _ in ()).throw(AssertionError('unauthorized body read'))
        try:
            handler._do_POST()
            assert responses[0][0] == 403
        finally:
            request_context.clear()
