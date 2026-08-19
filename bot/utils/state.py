
class UserState:
    """Tracks what each authorized user is currently doing."""

    def __init__(self):
        self.hex_wait = {}
        self.active_hex = {}

    def set_wait(self, uid, mode):
        self.hex_wait[uid] = mode

    def waiting(self, uid):
        return self.hex_wait.get(uid)

    def clear_wait(self, uid):
        self.hex_wait.pop(uid, None)

    def set_hex(self, uid, hex_str):
        self.active_hex[uid] = hex_str

    def get_hex(self, uid):
        return self.active_hex.get(uid)

    def clear_hex(self, uid):
        self.active_hex.pop(uid, None)

    def clear_all(self, uid):
        self.clear_wait(uid)
        self.clear_hex(uid)
