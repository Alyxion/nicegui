from fnmatch import fnmatch
from typing import Callable, Any, Optional, Dict, TYPE_CHECKING, Self

from nicegui import ui, core
from nicegui.context import context
from nicegui.elements.router_frame import RouterFrame
from nicegui.single_page_target import SinglePageTarget

PATH_RESOLVING_MAX_RECURSION = 100

if TYPE_CHECKING:
    from nicegui.single_page_router_config import SinglePageRouterConfig


class SinglePageRouter:
    """The SinglePageRouter manages the SinglePage navigation and content updates for a SinglePageApp instance.

    When ever a new page is opened, the SinglePageRouter exchanges the content of the current page with the content
    of the new page. The SinglePageRouter also manages the browser history and title updates.

    Multiple SinglePageRouters can be nested to create complex SinglePageApps with multiple content areas.

    See @ui.outlet or SinglePageApp for more information."""

    def __init__(self,
                 config: 'SinglePageRouterConfig',
                 included_paths: Optional[list[str]] = None,
                 excluded_paths: Optional[list[str]] = None,
                 use_browser_history: bool = True,
                 change_title: bool = True,
                 parent_router: 'SinglePageRouter' = None,
                 target_url: Optional[str] = None,
                 user_data: Optional[Dict] = None
                 ):
        """
        :param config: The SinglePageRouter which controls this router frame
        :param included_paths: A list of valid path masks which shall be allowed to be opened by the router
        :param excluded_paths: A list of path masks which shall be excluded from the router
        :param use_browser_history: Optional flag to enable or disable the browser history management. Default is True.
        :param change_title: Optional flag to enable or disable the title change. Default is True.
        :param target_url: The initial url of the router frame
        :param user_data: Optional user data which is passed to the builder functions of the router frame
        """
        super().__init__()
        self.router_config = config
        self.base_path = config.base_path
        if target_url is None:
            if parent_router is not None and parent_router.target_url is not None:
                target_url = parent_router.target_url
            else:
                target_url = self.router_config.base_path
        self.user_data = user_data
        self.child_routers: dict[str, "SinglePageRouter"] = {}
        self.use_browser_history = use_browser_history
        self.change_title = change_title
        self.parent_router = parent_router
        # split base path into it's elements
        base_path_elements = self.router_config.base_path.split('/')
        # replace all asterisks with the actual path elements from target url where possible
        target_url_elements = target_url.split('/')
        for i, element in enumerate(base_path_elements):
            if element.startswith("{") and element.endswith("}"):
                if i < len(target_url_elements):
                    base_path_elements[i] = target_url_elements[i]
        # repeat the same for all included paths
        if included_paths is not None:
            for i, path in enumerate(included_paths):
                path_elements = path.split('/')
                for j, element in enumerate(path_elements):
                    if element == '*':
                        if j < len(base_path_elements):
                            path_elements[j] = base_path_elements[j]
                included_paths[i] = '/'.join(path_elements)
        self.base_path = '/'.join(base_path_elements)
        if parent_router is not None:
            parent_router._register_child_router(included_paths[0], self)
        self.router_frame = RouterFrame(base_path=self.base_path,
                                        target_url=target_url,
                                        included_paths=included_paths,
                                        excluded_paths=excluded_paths,
                                        use_browser_history=use_browser_history,
                                        on_navigate=lambda url, history: self.navigate_to(url, history=history),
                                        user_data={'router': self})
        self._on_navigate: Optional[Callable[[str], Optional[str]]] = None
        self._on_resolve: Optional[Callable[[str], Optional[SinglePageTarget]]] = None
        self._on_open: Optional[Callable[[SinglePageTarget, Any], SinglePageTarget]] = None

    @property
    def target_url(self) -> str:
        """The current target url of the router frame

        :return: The target url of the router frame"""
        return self.router_frame.target_url

    def resolve_target(self, target: Any, user_data: Optional[Dict] = None) -> SinglePageTarget:
        """Resolves a URL or SPA target to a SinglePageUrl which contains details about the builder function to
        be called and the arguments to pass to the builder function.

        :param target: The target object such as a URL or Callable
        :param user_data: Optional user data which is passed to the resolver functions
        :return: The resolved SinglePageTarget object"""
        if self._on_resolve is not None:  # try custom handler first if defined
            resolved_target = self._on_resolve(target)
            if resolved_target is not None:
                return resolved_target
        if isinstance(target, SinglePageTarget):
            return target
        target = self.router_config.resolve_target(target)
        if target.valid and target.router is None:
            target.router = self
        if target is None:
            raise NotImplementedError
        if target.router_path is not None:
            original_path = target.original_path
            if self._on_open is not None:  # try the router instance's custom open handler first
                target = RouterFrame.run_safe(self._on_open(**user_data | {'target': target}))
                if target.original_path != original_path:
                    return self.resolve_target(target, user_data)
            rp = target.router_path
            if rp.on_open is not None:  # try the router path's custom open handler
                target = rp.on_open(target, **user_data)
                if target.original_path != original_path:
                    return self.resolve_target(target, user_data)
            config = self.router_config
            while config is not None:
                if config.on_open is not None:
                    target = config.on_open(target)
                    if target.original_path != original_path:
                        return self.resolve_target(target, user_data)
                config = config.parent_config
        return target

    def handle_navigate(self, target: str) -> Optional[str]:
        """Is called when there was a navigation event in the browser or by the navigate_to method.

        By default, the original target is returned. The SinglePageRouter and the router config (the outlet) can
        manipulate the target before it is resolved. If the target is None, the navigation is suppressed.

        :param target: The target URL
        :return: The target URL or None if the navigation is suppressed"""
        if self._on_navigate is not None:
            target = self._on_navigate(target)
            if target is None:
                return None
        new_target = self.router_config.handle_navigate(target)
        if new_target is None or new_target != target:
            return new_target
        return target

    def navigate_to(self, target: [SinglePageTarget, str], server_side=True, sync=False,
                    history: bool = True) -> None:
        """Open a new page in the browser by exchanging the content of the router frame

        :param target: The target page or url.
        :param server_side: Optional flag which defines if the call is originated on the server side and thus
            the browser history should be updated. Default is False.
        :param sync: Optional flag to define if the content should be updated synchronously. Default is False.
        :param history: Optional flag defining if the history setting shall be respected. Default is True."""
        # check if sub router is active and might handle the target
        if isinstance(target, str):
            for path_mask, frame in self.child_routers.items():
                if fnmatch(target, path_mask) or fnmatch(target, path_mask.rstrip('/') + '/*'):
                    frame.navigate_to(target, server_side)
                    return
            target = self.handle_navigate(target)
            if target is None:
                return
        handler_kwargs = SinglePageRouter.get_user_data() | self.user_data | self.router_frame.user_data | \
                    {'previous_url_path': self.router_frame.target_url}
        handler_kwargs['url_path'] = target if isinstance(target, str) else target.original_path
        target = self.resolve_target(target, user_data=handler_kwargs)
        if target is None or not target.valid:  # navigation suppressed
            return
        target_url = target.original_path
        handler_kwargs['target_url'] = target_url
        self._update_target_url(target_url)
        js_code = ''
        if history and self.use_browser_history:  # triggered by the browser
            js_code += f'window.history.pushState({{page: "{target_url}"}}, "", "{target_url}");'
        if target.builder is None:
            if target.fragment is not None:
                js_code += f'window.location.href = "#{target.fragment}";'  # go to fragment
                ui.run_javascript(js_code)
                return
            target = SinglePageTarget(builder=self._page_not_found, title='Page not found')
        if len(js_code) > 0:
            ui.run_javascript(js_code)
        handler_kwargs = {**target.path_args, **target.query_args, 'target': target} | handler_kwargs
        target_fragment = target.fragment
        if target.on_pre_update is not None:
            RouterFrame.run_safe(target.on_pre_update, **handler_kwargs)
        self.clear()
        self.router_frame.update_content(target.builder, handler_kwargs, target.title, target_fragment, sync)
        if self.change_title and target.builder and len(self.child_routers) == 0:
            # note: If the router is just a container for sub routers, the title is not updated here but
            # in the sub router's update_content method
            title = target.title if target.title is not None else core.app.config.title
            ui.page_title(title)
        if target.on_post_update is not None:
            RouterFrame.run_safe(target.on_post_update, handler_kwargs)

    def clear(self) -> None:
        """Clear the content of the router frame and removes all references to sub frames"""
        self.child_routers.clear()
        self.router_frame.clear()

    def update_user_data(self, new_data: dict) -> None:
        """Update the user data of the router frame

        :param new_data: The new user data to set"""
        self.user_data.update(new_data)

    def on_navigate(self, callback: Callable[[str], Optional[str]]) -> Self:
        """Set the on navigate callback which is called when a navigation event is triggered

        :param callback: The callback function"""
        self._on_navigate = callback
        return self

    def on_resolve(self, callback: Callable[[str], Optional[SinglePageTarget]]) -> Self:
        """Set the on resolve callback which is called when a URL path is resolved to a target

        :param callback: The callback function"""
        self._on_resolve = callback
        return self

    def on_open(self, callback: Callable[[SinglePageTarget, Any], SinglePageTarget]) -> Self:
        """Set the on open callback which is called when a target is opened

        :param callback: The callback function"""
        self._on_open = callback
        return self

    @staticmethod
    def get_user_data() -> Dict:
        """Returns a combined dictionary of all user data of the parent router frames"""
        result_dict = {}
        for slot in context.slot_stack:
            if isinstance(slot.parent, RouterFrame):
                result_dict.update(slot.parent.user_data['router'].user_data)
        return result_dict

    @staticmethod
    def get_current_router() -> Optional['SinglePageRouter']:
        """Get the current router frame from the context stack

        :return: The current router or None if no router in the context stack"""
        for slot in reversed(context.slot_stack):  # we need to inform the parent router frame about
            if isinstance(slot.parent, RouterFrame):  # our existence so it can navigate to our pages
                return slot.parent.user_data['router']
        return None

    def _register_child_router(self, path: str, frame: 'SinglePageRouter') -> None:
        """Registers a child router which handles a certain sub path

        :param path: The path of the child router
        :param frame: The child router"""
        self.child_routers[path] = frame
        self.router_frame.child_frame_paths = list(self.child_routers.keys())

    @staticmethod
    def _page_not_found() -> None:
        """Default builder function for the page not found error page"""
        ui.label(f'Oops! Page Not Found 🚧').classes('text-3xl')
        ui.label(f'Sorry, the page you are looking for could not be found. 😔')

    def _update_target_url(self, target_url: str) -> None:
        """Updates the target url of the router and all parent routers

        :param target_url: The new target url"""
        cur_router = self
        for _ in range(PATH_RESOLVING_MAX_RECURSION):
            cur_router.router_frame.target_url = target_url
            cur_router = cur_router.parent_router
            if cur_router is None:
                return