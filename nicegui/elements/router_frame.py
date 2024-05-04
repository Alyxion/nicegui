import inspect
import typing
from typing import Callable, Any, Optional, Self

from nicegui import ui, helpers, background_tasks, core
from nicegui.single_page_target import SinglePageTarget

if typing.TYPE_CHECKING:
    from nicegui.single_page_router import SinglePageRouter


class RouterFrame(ui.element, component='router_frame.js'):
    """The RouterFrame is a special element which is used by the SinglePageRouter to exchange the content of
    the current page with the content of the new page. It serves as container and overrides the browser's history
    management to prevent the browser from reloading the whole page."""

    def __init__(self,
                 router: "SinglePageRouter",
                 included_paths: Optional[list[str]] = None,
                 excluded_paths: Optional[list[str]] = None,
                 use_browser_history: bool = True,
                 change_title: bool = True,
                 parent_router_frame: "RouterFrame" = None,
                 target_url: Optional[str] = None):
        """
        :param router: The SinglePageRouter which controls this router frame
        :param included_paths: A list of valid path masks which shall be allowed to be opened by the router
        :param excluded_paths: A list of path masks which shall be excluded from the router
        :param use_browser_history: Optional flag to enable or disable the browser history management. Default is True.
        :param change_title: Optional flag to enable or disable the title change. Default is True.
        :param target_url: The initial url of the router frame
        """
        super().__init__()
        self.router = router
        included_masks = []
        excluded_masks = []
        if included_paths is not None:
            for path in included_paths:
                cleaned = path.rstrip('/')
                included_masks.append(cleaned)
                included_masks.append(cleaned + '/*')
        if excluded_paths is not None:
            for path in excluded_paths:
                cleaned = path.rstrip('/')
                excluded_masks.append(cleaned)
                excluded_masks.append(cleaned + '/*')
        if target_url is None:
            if parent_router_frame is not None and parent_router_frame._props['target_url'] is not None:
                target_url = parent_router_frame._props['target_url']
            else:
                target_url = self.router.base_path
        self._props['target_url'] = target_url
        self._props['included_path_masks'] = included_masks
        self._props['excluded_path_masks'] = excluded_masks
        self._props['base_path'] = self.router.base_path
        self._props['browser_history'] = use_browser_history
        self._props['child_frames'] = []
        self.child_frames: dict[str, "RouterFrame"] = {}
        self.use_browser_history = use_browser_history
        self.change_title = change_title
        self.parent_frame = parent_router_frame
        if parent_router_frame is not None:
            parent_router_frame._register_sub_frame(included_paths[0], self)
        self._on_resolve: Optional[Callable[[Any], SinglePageTarget]] = None
        self.on('open', lambda e: self.navigate_to(e.args, _server_side=False))

    @property
    def target_url(self) -> str:
        """The current target url of the router frame"""
        return self._props['target_url']

    def on_resolve(self, on_resolve: Callable[[Any], SinglePageTarget]) -> Self:
        """Set the on_resolve function which is used to resolve the target to a SinglePageUrl

        :param on_resolve: The on_resolve function which receives a target object such as an URL or Callable and
            returns a SinglePageUrl object."""
        self._on_resolve = on_resolve
        return self

    def resolve_target(self, target: Any) -> SinglePageTarget:
        """Resolves a URL or SPA target to a SinglePageUrl which contains details about the builder function to
        be called and the arguments to pass to the builder function.

        :param target: The target object such as a URL or Callable
        :return: The resolved SinglePageUrl object"""
        if isinstance(target, SinglePageTarget):
            return target
        if self._on_resolve is not None:
            return self._on_resolve(target)
        raise NotImplementedError

    def navigate_to(self, target: [SinglePageTarget, str], _server_side=True, _sync=False) -> None:
        """Open a new page in the browser by exchanging the content of the router frame

        :param target: The target page or url.
        :param _server_side: Optional flag which defines if the call is originated on the server side and thus
            the browser history should be updated. Default is False."""
        # check if sub router is active and might handle the target
        for path_mask, frame in self.child_frames.items():
            if path_mask == target or target.startswith(path_mask + '/'):
                frame.navigate_to(target, _server_side)
                return
        target_url = self.resolve_target(target)
        entry = target_url.entry
        if entry is None:
            if target_url.fragment is not None:
                ui.run_javascript(f'window.location.href = "#{target_url.fragment}";')  # go to fragment
                return
            title = "Page not found"
            builder = self._page_not_found
        else:
            builder = entry.builder
            title = entry.title
        if _server_side and self.use_browser_history:
            ui.run_javascript(
                f'window.history.pushState({{page: "{target_url.original_path}"}}, "", "{target_url.original_path}");')
        self._props['target_url'] = target_url.original_path
        builder_kwargs = {**target_url.path_args, **target_url.query_args}
        if "url_path" not in builder_kwargs:
            builder_kwargs["url_path"] = target_url.original_path
        target_fragment = target_url.fragment
        self.update_content(builder, builder_kwargs, title, target_fragment, _sync=_sync)

    def update_content(self, builder, builder_kwargs, title, target_fragment, _sync=False):
        """Update the content of the router frame

        :param builder: The builder function which builds the content of the page
        :param builder_kwargs: The keyword arguments to pass to the builder function
        :param title: The title of the page
        :param target_fragment: The fragment to navigate to after the content has been loaded"""
        if self.change_title:
            ui.page_title(title if title is not None else core.app.config.title)

        def exec_builder():
            """Execute the builder function with the given keyword arguments"""
            args = inspect.signature(builder).parameters.keys()
            kwargs = {k: v for k, v in builder_kwargs.items() if k in args}
            return builder(**kwargs)

        async def build() -> None:
            with self:
                result = exec_builder()
                if helpers.is_coroutine_function(builder):
                    await result
                if target_fragment is not None:
                    await ui.run_javascript(f'window.location.href = "#{target_fragment}";')

        self.clear()
        if _sync:
            with self:
                exec_builder()
                if target_fragment is not None:
                    ui.run_javascript(f'window.location.href = "#{target_fragment}";')
        else:
            background_tasks.create(build())

    def clear(self) -> None:
        """Clear the content of the router frame and removes all references to sub frames"""
        self.child_frames.clear()
        self._props['child_frame_paths'] = []
        super().clear()

    def _register_sub_frame(self, path: str, frame: "RouterFrame") -> None:
        """Registers a sub frame to the router frame

        :param path: The path of the sub frame
        :param frame: The sub frame"""
        self.child_frames[path] = frame
        self._props['child_frame_paths'] = list(self.child_frames.keys())

    def _page_not_found(self, url_path: str):
        """
        Default builder function for the page not found error page
        """
        ui.label(f'Oops! Page Not Found 🚧').classes('text-3xl')
        ui.label(f'Sorry, the page you are looking for could not be found. 😔')
