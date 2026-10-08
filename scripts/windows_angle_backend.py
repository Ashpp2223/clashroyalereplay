"""Thin Windows x64 EGL/GLES bridge for the ARM64 libg host experiment.

This module calls a local ANGLE DLL pair. Its handles and function pointers are
Windows x64 values: a guest ARM64 caller must enter through the emulator's
import hook, and must never jump to a value returned by get_proc_address().

Self-check without Android or a visible window::

    python scripts/windows_angle_backend.py --dll-directory \
        "C:\\Program Files\\obs-studio\\obs-plugins\\64bit"
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
from typing import Sequence


EGL_NONE = 0x3038
EGL_VERSION = 0x3054
GL_VERSION = 0x1F02


class WindowsAngleBackend:
    """Call one local AMD64 ANGLE DLL pair with real EGL return semantics."""

    def __init__(self, dll_directory: str | Path) -> None:
        if os.name != "nt":
            raise OSError("ANGLE DLL backend requires Windows")
        directory = Path(dll_directory).expanduser().resolve(strict=True)
        egl_path = directory / "libEGL.dll"
        gles_path = directory / "libGLESv2.dll"
        if not egl_path.is_file() or not gles_path.is_file():
            raise FileNotFoundError(f"ANGLE DLL pair is missing in {directory}")

        self.dll_directory = directory
        self._dll_directory_handle = os.add_dll_directory(str(directory))
        try:
            self.egl = ctypes.WinDLL(str(egl_path))
            self.gles = ctypes.WinDLL(str(gles_path))
            self._bind()
        except BaseException:
            self._dll_directory_handle.close()
            raise

    def close(self) -> None:
        """Remove the DLL lookup path; already loaded DLLs stay in this process."""
        self._dll_directory_handle.close()

    def __enter__(self) -> WindowsAngleBackend:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close()

    def _bind(self) -> None:
        pointer = ctypes.c_void_p
        integer = ctypes.c_int
        boolean = ctypes.c_uint
        integer_out = ctypes.POINTER(integer)
        config_out = ctypes.POINTER(pointer)

        signatures = {
            "eglGetDisplay": (pointer, [pointer]),
            "eglInitialize": (boolean, [pointer, integer_out, integer_out]),
            "eglGetConfigs": (boolean, [pointer, config_out, integer, integer_out]),
            "eglChooseConfig": (boolean, [pointer, integer_out, config_out, integer, integer_out]),
            "eglGetConfigAttrib": (boolean, [pointer, pointer, integer, integer_out]),
            "eglCreateContext": (pointer, [pointer, pointer, pointer, integer_out]),
            "eglCreatePbufferSurface": (pointer, [pointer, pointer, integer_out]),
            "eglMakeCurrent": (boolean, [pointer, pointer, pointer, pointer]),
            "eglGetCurrentContext": (pointer, []),
            "eglSwapBuffers": (boolean, [pointer, pointer]),
            "eglGetProcAddress": (pointer, [ctypes.c_char_p]),
            "eglQueryString": (pointer, [pointer, integer]),
            "eglDestroySurface": (boolean, [pointer, pointer]),
            "eglDestroyContext": (boolean, [pointer, pointer]),
            "eglTerminate": (boolean, [pointer]),
            "eglGetError": (integer, []),
        }
        for name, (restype, argtypes) in signatures.items():
            function = getattr(self.egl, name)
            function.restype = restype
            function.argtypes = argtypes
        self.gles.glGetString.restype = pointer
        self.gles.glGetString.argtypes = [ctypes.c_uint]
        self.gles.glGetStringi.restype = pointer
        self.gles.glGetStringi.argtypes = [ctypes.c_uint, ctypes.c_uint]
        self.gles.glGetIntegerv.restype = None
        self.gles.glGetIntegerv.argtypes = [ctypes.c_uint, integer_out]
        self.gles.glGetError.restype = ctypes.c_uint
        self.gles.glGetError.argtypes = []
        self.gles.glGetShaderPrecisionFormat.restype = None
        self.gles.glGetShaderPrecisionFormat.argtypes = [
            ctypes.c_uint, ctypes.c_uint, integer_out, integer_out,
        ]
        self.gles.glGenVertexArrays.restype = None
        self.gles.glGenVertexArrays.argtypes = [integer, ctypes.POINTER(ctypes.c_uint)]
        self.gles.glDeleteVertexArrays.restype = None
        self.gles.glDeleteVertexArrays.argtypes = [integer, ctypes.POINTER(ctypes.c_uint)]
        self.gles.glBindVertexArray.restype = None
        self.gles.glBindVertexArray.argtypes = [ctypes.c_uint]
        texture_signatures = {
            "glGenTextures": [integer, ctypes.POINTER(ctypes.c_uint)],
            "glDeleteTextures": [integer, ctypes.POINTER(ctypes.c_uint)],
            "glActiveTexture": [ctypes.c_uint],
            "glBindTexture": [ctypes.c_uint, ctypes.c_uint],
            "glTexParameteri": [ctypes.c_uint, ctypes.c_uint, integer],
            "glPixelStorei": [ctypes.c_uint, integer],
            "glTexImage2D": [
                ctypes.c_uint, integer, integer, integer, integer, integer,
                ctypes.c_uint, ctypes.c_uint, pointer,
            ],
            "glTexSubImage2D": [
                ctypes.c_uint, integer, integer, integer, integer, integer,
                ctypes.c_uint, ctypes.c_uint, pointer,
            ],
            "glTexStorage2D": [ctypes.c_uint, integer, ctypes.c_uint, integer, integer],
            "glGenerateMipmap": [ctypes.c_uint],
        }
        for name, argtypes in texture_signatures.items():
            try:
                function = getattr(self.gles, name)
            except AttributeError as error:
                raise RuntimeError(f"ANGLE GLES export {name} is unavailable") from error
            function.restype = None
            function.argtypes = argtypes
        framebuffer_signatures = {
            "glGenFramebuffers": (None, [integer, ctypes.POINTER(ctypes.c_uint)]),
            "glDeleteFramebuffers": (None, [integer, ctypes.POINTER(ctypes.c_uint)]),
            "glBindFramebuffer": (None, [ctypes.c_uint, ctypes.c_uint]),
            "glFramebufferTexture2D": (
                None, [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, integer],
            ),
            "glCheckFramebufferStatus": (ctypes.c_uint, [ctypes.c_uint]),
            "glGenRenderbuffers": (None, [integer, ctypes.POINTER(ctypes.c_uint)]),
            "glDeleteRenderbuffers": (None, [integer, ctypes.POINTER(ctypes.c_uint)]),
            "glBindRenderbuffer": (None, [ctypes.c_uint, ctypes.c_uint]),
            "glRenderbufferStorage": (
                None, [ctypes.c_uint, ctypes.c_uint, integer, integer],
            ),
            "glRenderbufferStorageMultisample": (
                None, [ctypes.c_uint, integer, ctypes.c_uint, integer, integer],
            ),
            "glFramebufferRenderbuffer": (
                None, [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint],
            ),
        }
        for name, (restype, argtypes) in framebuffer_signatures.items():
            try:
                function = getattr(self.gles, name)
            except AttributeError as error:
                raise RuntimeError(f"ANGLE GLES export {name} is unavailable") from error
            function.restype = restype
            function.argtypes = argtypes
        state_signatures = {
            "glDisable": [ctypes.c_uint],
            "glEnable": [ctypes.c_uint],
            "glViewport": [integer, integer, integer, integer],
            "glDepthRangef": [ctypes.c_float, ctypes.c_float],
            "glScissor": [integer, integer, integer, integer],
            "glClearColor": [ctypes.c_float] * 4,
            "glClear": [ctypes.c_uint],
            "glFlush": [],
            "glDepthMask": [ctypes.c_ubyte],
            "glBlendFunc": [ctypes.c_uint, ctypes.c_uint],
            "glReadBuffer": [ctypes.c_uint],
            "glDrawBuffers": [integer, ctypes.POINTER(ctypes.c_uint)],
            "glClearBufferfv": [ctypes.c_uint, integer, ctypes.POINTER(ctypes.c_float)],
            "glStencilMask": [ctypes.c_uint],
            "glClearBufferiv": [ctypes.c_uint, integer, integer_out],
        }
        for name, argtypes in state_signatures.items():
            try:
                function = getattr(self.gles, name)
            except AttributeError as error:
                raise RuntimeError(f"ANGLE GLES export {name} is unavailable") from error
            function.restype = None
            function.argtypes = argtypes
        buffer_signatures = {
            "glGenBuffers": [integer, ctypes.POINTER(ctypes.c_uint)],
            "glDeleteBuffers": [integer, ctypes.POINTER(ctypes.c_uint)],
            "glBindBuffer": [ctypes.c_uint, ctypes.c_uint],
            "glBufferData": [ctypes.c_uint, ctypes.c_ssize_t, pointer, ctypes.c_uint],
            "glBufferSubData": [ctypes.c_uint, ctypes.c_ssize_t, ctypes.c_ssize_t, pointer],
            "glBindBufferBase": [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint],
        }
        for name, argtypes in buffer_signatures.items():
            try:
                function = getattr(self.gles, name)
            except AttributeError as error:
                raise RuntimeError(f"ANGLE GLES export {name} is unavailable") from error
            function.restype = None
            function.argtypes = argtypes
        sampler_signatures = {
            "glGenSamplers": [integer, ctypes.POINTER(ctypes.c_uint)],
            "glDeleteSamplers": [integer, ctypes.POINTER(ctypes.c_uint)],
            "glSamplerParameteri": [ctypes.c_uint, ctypes.c_uint, integer],
            "glBindSampler": [ctypes.c_uint, ctypes.c_uint],
        }
        for name, argtypes in sampler_signatures.items():
            try:
                function = getattr(self.gles, name)
            except AttributeError as error:
                raise RuntimeError(f"ANGLE GLES export {name} is unavailable") from error
            function.restype = None
            function.argtypes = argtypes

    @staticmethod
    def _attributes(values: Sequence[int]) -> ctypes.Array[ctypes.c_int]:
        items = tuple(int(value) for value in values)
        if not items or items[-1] != EGL_NONE or len(items) % 2 != 1:
            raise ValueError("EGL attributes must be key/value pairs ending in EGL_NONE")
        return (ctypes.c_int * len(items))(*items)

    def get_display(self, native_display: int = 0) -> int:
        return int(self.egl.eglGetDisplay(native_display) or 0)

    def initialize(self, display: int) -> tuple[bool, int, int]:
        major = ctypes.c_int()
        minor = ctypes.c_int()
        ok = bool(self.egl.eglInitialize(display, ctypes.byref(major), ctypes.byref(minor)))
        return ok, major.value, minor.value

    def get_configs(self, display: int, max_configs: int = 0) -> tuple[bool, list[int], int]:
        if max_configs < 0:
            raise ValueError("max_configs must be nonnegative")
        configs = (ctypes.c_void_p * max_configs)() if max_configs else None
        count = ctypes.c_int()
        ok = bool(self.egl.eglGetConfigs(display, configs, max_configs, ctypes.byref(count)))
        found = [int(configs[index] or 0) for index in range(min(max_configs, count.value))] if configs else []
        return ok, found, count.value

    def choose_config(
        self, display: int, attributes: Sequence[int], max_configs: int = 1
    ) -> tuple[bool, list[int], int]:
        if max_configs < 0:
            raise ValueError("max_configs must be nonnegative")
        keys = self._attributes(attributes)
        configs = (ctypes.c_void_p * max_configs)() if max_configs else None
        count = ctypes.c_int()
        ok = bool(self.egl.eglChooseConfig(display, keys, configs, max_configs, ctypes.byref(count)))
        found = [int(configs[index] or 0) for index in range(min(max_configs, count.value))] if configs else []
        return ok, found, count.value

    def get_config_attrib(self, display: int, config: int, attribute: int) -> tuple[bool, int]:
        value = ctypes.c_int()
        ok = bool(self.egl.eglGetConfigAttrib(display, config, attribute, ctypes.byref(value)))
        return ok, value.value

    def create_context(self, display: int, config: int, client_version: int, share: int = 0) -> int:
        attrs = self._attributes((0x3098, client_version, EGL_NONE))
        return int(self.egl.eglCreateContext(display, config, share, attrs) or 0)

    def create_pbuffer_surface(self, display: int, config: int, width: int, height: int) -> int:
        if width <= 0 or height <= 0:
            raise ValueError("pbuffer dimensions must be positive")
        attrs = self._attributes((0x3057, width, 0x3056, height, EGL_NONE))
        return int(self.egl.eglCreatePbufferSurface(display, config, attrs) or 0)

    def make_current(self, display: int, surface: int, context: int) -> bool:
        """Bind the context on this host thread after each guest GL thread switch.

        The guest scheduler owns its guest-thread-to-context map. ANGLE's
        current context is native thread-local state, so a scheduler that runs
        several guest threads on one host thread must rebind at every switch.
        Passing zero for both surface and context unbinds that host thread.
        """
        return bool(self.egl.eglMakeCurrent(display, surface, surface, context))

    def get_current_context(self) -> int:
        return int(self.egl.eglGetCurrentContext() or 0)

    def swap_buffers(self, display: int, surface: int) -> bool:
        return bool(self.egl.eglSwapBuffers(display, surface))

    def get_proc_address(self, name: str) -> int:
        """Return an x64 address for host-side calls, never for guest execution."""
        return int(self.egl.eglGetProcAddress(name.encode("ascii")) or 0)

    def get_gl_string(self, name: int = GL_VERSION) -> bytes | None:
        address = self.gles.glGetString(name)
        return ctypes.string_at(address) if address else None

    def get_gl_string_i(self, name: int, index: int) -> bytes | None:
        address = self.gles.glGetStringi(name, index)
        return ctypes.string_at(address) if address else None

    def get_gl_integers(self, name: int, count: int = 1) -> list[int]:
        """Read caller-selected output width; some GL names return arrays."""
        if count < 1:
            raise ValueError("GL integer count must be positive")
        values = (ctypes.c_int * count)()
        self.gles.glGetIntegerv(name, values)
        return list(values)

    def get_gl_error(self) -> int:
        return int(self.gles.glGetError())

    def get_shader_precision_format(self, shader_type: int, precision_type: int) -> tuple[int, int, int]:
        exponent_range = (ctypes.c_int * 2)()
        precision = ctypes.c_int()
        self.gles.glGetShaderPrecisionFormat(
            shader_type, precision_type, exponent_range, ctypes.byref(precision)
        )
        return exponent_range[0], exponent_range[1], precision.value

    def gen_vertex_arrays(self, count: int) -> list[int]:
        if count < 0:
            raise ValueError("GL object count must be nonnegative")
        objects = (ctypes.c_uint * count)()
        self.gles.glGenVertexArrays(count, objects)
        return list(objects)

    def delete_vertex_arrays(self, objects: Sequence[int]) -> None:
        names = (ctypes.c_uint * len(objects))(*(int(item) for item in objects))
        self.gles.glDeleteVertexArrays(len(objects), names)

    def bind_vertex_array(self, object_name: int) -> None:
        self.gles.glBindVertexArray(object_name)

    def gen_textures(self, count: int) -> list[int]:
        if count < 0:
            raise ValueError("GL object count must be nonnegative")
        objects = (ctypes.c_uint * count)()
        self.gles.glGenTextures(count, objects)
        return list(objects)

    def delete_textures(self, objects: Sequence[int]) -> None:
        names = (ctypes.c_uint * len(objects))(*(int(item) for item in objects))
        self.gles.glDeleteTextures(len(objects), names)

    def active_texture(self, texture_unit: int) -> None:
        self.gles.glActiveTexture(texture_unit)

    def bind_texture(self, target: int, texture: int) -> None:
        self.gles.glBindTexture(target, texture)

    def tex_parameter_i(self, target: int, name: int, value: int) -> None:
        self.gles.glTexParameteri(target, name, value)

    def pixel_store_i(self, name: int, value: int) -> None:
        self.gles.glPixelStorei(name, value)

    @staticmethod
    def _byte_buffer(data: bytes | bytearray | memoryview | None) -> ctypes.Array[ctypes.c_char] | None:
        """Hold caller bytes in host memory for the duration of a GL call."""
        if data is None:
            return None
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("data must be bytes-like or None, never a guest pointer")
        return ctypes.create_string_buffer(bytes(data))

    def tex_image_2d(
        self, target: int, level: int, internal_format: int, width: int, height: int,
        border: int, pixel_format: int, pixel_type: int,
        pixels: bytes | bytearray | memoryview | None,
    ) -> None:
        buffer = self._byte_buffer(pixels)
        self.gles.glTexImage2D(
            target, level, internal_format, width, height, border,
            pixel_format, pixel_type, buffer,
        )

    def tex_sub_image_2d(
        self, target: int, level: int, xoffset: int, yoffset: int,
        width: int, height: int, pixel_format: int, pixel_type: int,
        pixels: bytes | bytearray | memoryview | None,
    ) -> None:
        buffer = self._byte_buffer(pixels)
        self.gles.glTexSubImage2D(
            target, level, xoffset, yoffset, width, height,
            pixel_format, pixel_type, buffer,
        )

    def tex_storage_2d(
        self, target: int, levels: int, internal_format: int, width: int, height: int,
    ) -> None:
        self.gles.glTexStorage2D(target, levels, internal_format, width, height)

    def generate_mipmap(self, target: int) -> None:
        self.gles.glGenerateMipmap(target)

    def gen_framebuffers(self, count: int) -> list[int]:
        if count < 0:
            raise ValueError("GL object count must be nonnegative")
        objects = (ctypes.c_uint * count)()
        self.gles.glGenFramebuffers(count, objects)
        return list(objects)

    def bind_framebuffer(self, target: int, framebuffer: int) -> None:
        self.gles.glBindFramebuffer(target, framebuffer)

    def delete_framebuffers(self, objects: Sequence[int]) -> None:
        names = (ctypes.c_uint * len(objects))(*(int(item) for item in objects))
        self.gles.glDeleteFramebuffers(len(objects), names)

    def framebuffer_texture_2d(
        self, target: int, attachment: int, texture_target: int, texture: int, level: int,
    ) -> None:
        self.gles.glFramebufferTexture2D(target, attachment, texture_target, texture, level)

    def check_framebuffer_status(self, target: int) -> int:
        return int(self.gles.glCheckFramebufferStatus(target))

    def gen_renderbuffers(self, count: int) -> list[int]:
        if count < 0:
            raise ValueError("GL object count must be nonnegative")
        objects = (ctypes.c_uint * count)()
        self.gles.glGenRenderbuffers(count, objects)
        return list(objects)

    def delete_renderbuffers(self, objects: Sequence[int]) -> None:
        names = (ctypes.c_uint * len(objects))(*(int(item) for item in objects))
        self.gles.glDeleteRenderbuffers(len(objects), names)

    def bind_renderbuffer(self, target: int, renderbuffer: int) -> None:
        self.gles.glBindRenderbuffer(target, renderbuffer)

    def renderbuffer_storage(
        self, target: int, internal_format: int, width: int, height: int,
    ) -> None:
        self.gles.glRenderbufferStorage(target, internal_format, width, height)

    def renderbuffer_storage_multisample(
        self, target: int, samples: int, internal_format: int, width: int, height: int,
    ) -> None:
        self.gles.glRenderbufferStorageMultisample(target, samples, internal_format, width, height)

    def framebuffer_renderbuffer(
        self, target: int, attachment: int, renderbuffer_target: int, renderbuffer: int,
    ) -> None:
        self.gles.glFramebufferRenderbuffer(target, attachment, renderbuffer_target, renderbuffer)

    def disable(self, capability: int) -> None:
        self.gles.glDisable(capability)

    def enable(self, capability: int) -> None:
        self.gles.glEnable(capability)

    def viewport(self, x: int, y: int, width: int, height: int) -> None:
        self.gles.glViewport(x, y, width, height)

    def depth_range_f(self, near: float, far: float) -> None:
        self.gles.glDepthRangef(near, far)

    def scissor(self, x: int, y: int, width: int, height: int) -> None:
        self.gles.glScissor(x, y, width, height)

    def clear_color(self, red: float, green: float, blue: float, alpha: float) -> None:
        self.gles.glClearColor(red, green, blue, alpha)

    def clear(self, mask: int) -> None:
        self.gles.glClear(mask)

    def flush(self) -> None:
        self.gles.glFlush()

    def depth_mask(self, enabled: bool) -> None:
        self.gles.glDepthMask(enabled)

    def blend_func(self, source_factor: int, destination_factor: int) -> None:
        self.gles.glBlendFunc(source_factor, destination_factor)

    def read_buffer(self, source: int) -> None:
        self.gles.glReadBuffer(source)

    def draw_buffers(self, buffers: Sequence[int]) -> None:
        names = (ctypes.c_uint * len(buffers))(*(int(item) for item in buffers))
        self.gles.glDrawBuffers(len(buffers), names)

    def clear_buffer_fv(self, buffer: int, draw_buffer: int, values: Sequence[float]) -> None:
        required = {0x1800: 4, 0x1801: 1}  # GL_COLOR, GL_DEPTH
        if buffer not in required or len(values) != required[buffer]:
            raise ValueError("glClearBufferfv needs four color values or one depth value")
        components = (ctypes.c_float * len(values))(*(float(value) for value in values))
        self.gles.glClearBufferfv(buffer, draw_buffer, components)

    def stencil_mask(self, mask: int) -> None:
        self.gles.glStencilMask(mask)

    def clear_buffer_iv(self, buffer: int, draw_buffer: int, values: Sequence[int]) -> None:
        required = {0x1800: 4, 0x1802: 1}  # GL_COLOR, GL_STENCIL
        if buffer not in required or len(values) != required[buffer]:
            raise ValueError("glClearBufferiv needs four color values or one stencil value")
        components = (ctypes.c_int * len(values))(*(int(value) for value in values))
        self.gles.glClearBufferiv(buffer, draw_buffer, components)

    def gen_buffers(self, count: int) -> list[int]:
        if count < 0:
            raise ValueError("GL object count must be nonnegative")
        objects = (ctypes.c_uint * count)()
        self.gles.glGenBuffers(count, objects)
        return list(objects)

    def delete_buffers(self, objects: Sequence[int]) -> None:
        names = (ctypes.c_uint * len(objects))(*(int(item) for item in objects))
        self.gles.glDeleteBuffers(len(objects), names)

    def bind_buffer(self, target: int, buffer: int) -> None:
        self.gles.glBindBuffer(target, buffer)

    def buffer_data(
        self, target: int, size: int, data: bytes | bytearray | memoryview | None, usage: int,
    ) -> None:
        if size < 0:
            raise ValueError("GL buffer size must be nonnegative")
        host_data = self._byte_buffer(data)
        if host_data is not None and len(host_data) - 1 < size:
            raise ValueError("GL buffer data is shorter than the requested size")
        self.gles.glBufferData(target, size, host_data, usage)

    def buffer_sub_data(
        self, target: int, offset: int, data: bytes | bytearray | memoryview,
    ) -> None:
        if offset < 0:
            raise ValueError("GL buffer offset must be nonnegative")
        host_data = self._byte_buffer(data)
        if host_data is None:
            raise TypeError("GL buffer update needs bytes-like data")
        self.gles.glBufferSubData(target, offset, len(host_data) - 1, host_data)

    def bind_buffer_base(self, target: int, index: int, buffer: int) -> None:
        self.gles.glBindBufferBase(target, index, buffer)

    def gen_samplers(self, count: int) -> list[int]:
        if count < 0:
            raise ValueError("GL object count must be nonnegative")
        objects = (ctypes.c_uint * count)()
        self.gles.glGenSamplers(count, objects)
        return list(objects)

    def delete_samplers(self, objects: Sequence[int]) -> None:
        names = (ctypes.c_uint * len(objects))(*(int(item) for item in objects))
        self.gles.glDeleteSamplers(len(objects), names)

    def sampler_parameter_i(self, sampler: int, name: int, value: int) -> None:
        self.gles.glSamplerParameteri(sampler, name, value)

    def bind_sampler(self, unit: int, sampler: int) -> None:
        self.gles.glBindSampler(unit, sampler)

    def query_egl_string(self, display: int, name: int = EGL_VERSION) -> bytes | None:
        address = self.egl.eglQueryString(display, name)
        return ctypes.string_at(address) if address else None

    def destroy_surface(self, display: int, surface: int) -> bool:
        return bool(self.egl.eglDestroySurface(display, surface))

    def destroy_context(self, display: int, context: int) -> bool:
        return bool(self.egl.eglDestroyContext(display, context))

    def terminate(self, display: int) -> bool:
        return bool(self.egl.eglTerminate(display))

    def get_error(self) -> int:
        """Return and clear the native thread's latest EGL error."""
        return int(self.egl.eglGetError())

    def self_check(self) -> dict[str, object]:
        """Exercise a real offscreen GLES3 context and release every EGL handle."""
        display = self.get_display()
        if not display:
            raise RuntimeError(f"eglGetDisplay failed: EGL error 0x{self.get_error():04x}")
        initialized = False
        surface = 0
        context = 0
        current = False
        vertex_arrays: list[int] = []
        textures: list[int] = []
        framebuffers: list[int] = []
        renderbuffers: list[int] = []
        buffers: list[int] = []
        samplers: list[int] = []
        cleanup: dict[str, bool] = {}
        result: dict[str, object] = {"dll_directory": str(self.dll_directory)}
        try:
            ok, major, minor = self.initialize(display)
            if not ok:
                raise RuntimeError(f"eglInitialize failed: EGL error 0x{self.get_error():04x}")
            initialized = True
            result["egl_version"] = [major, minor]
            result["egl_version_string"] = (self.query_egl_string(display) or b"").decode("ascii", "replace")

            attrs = (
                0x3024, 8, 0x3023, 8, 0x3022, 8, 0x3021, 8,
                0x3025, 24, 0x3026, 8, 0x3040, 0x40, 0x3033, 1, EGL_NONE,
            )
            ok, configs, count = self.choose_config(display, attrs)
            if not ok or count < 1 or not configs or not configs[0]:
                raise RuntimeError(f"eglChooseConfig failed: EGL error 0x{self.get_error():04x}")
            config = configs[0]
            selected: dict[str, int] = {}
            for label, attribute in (
                ("red", 0x3024), ("green", 0x3023), ("blue", 0x3022), ("alpha", 0x3021),
                ("depth", 0x3025), ("stencil", 0x3026), ("renderable_type", 0x3040),
                ("surface_type", 0x3033),
            ):
                valid, value = self.get_config_attrib(display, config, attribute)
                if not valid:
                    raise RuntimeError(f"eglGetConfigAttrib({label}) failed: EGL error 0x{self.get_error():04x}")
                selected[label] = value
            result["config"] = selected

            surface = self.create_pbuffer_surface(display, config, 64, 64)
            if not surface:
                raise RuntimeError(f"eglCreatePbufferSurface failed: EGL error 0x{self.get_error():04x}")
            context = self.create_context(display, config, 3)
            if not context:
                raise RuntimeError(f"eglCreateContext failed: EGL error 0x{self.get_error():04x}")
            if not self.make_current(display, surface, context):
                raise RuntimeError(f"eglMakeCurrent failed: EGL error 0x{self.get_error():04x}")
            current = True
            if self.get_current_context() != context:
                raise RuntimeError("eglGetCurrentContext did not return the created context")
            version = self.get_gl_string()
            if not version:
                raise RuntimeError("glGetString(GL_VERSION) returned null")
            result["gl_version"] = version.decode("ascii", "replace")
            major_gl = self.get_gl_integers(0x821B)[0]
            minor_gl = self.get_gl_integers(0x821C)[0]
            extensions = self.get_gl_integers(0x821D)[0]
            result["gl_version_numbers"] = [major_gl, minor_gl]
            result["gl_extension_count"] = extensions
            if major_gl < 3 or extensions < 0:
                raise RuntimeError("ANGLE did not provide the requested GLES3 context")
            if extensions and not self.get_gl_string_i(0x1F03, 0):
                raise RuntimeError("glGetStringi(GL_EXTENSIONS, 0) returned null")
            result["vertex_high_float_precision"] = list(
                self.get_shader_precision_format(0x8B31, 0x8DF2)
            )
            vertex_arrays = self.gen_vertex_arrays(1)
            textures = self.gen_textures(2)
            if not vertex_arrays[0] or not all(textures):
                raise RuntimeError("GL object creation returned a zero name")
            self.bind_vertex_array(vertex_arrays[0])
            self.active_texture(0x84C0)  # GL_TEXTURE0
            self.bind_texture(0x0DE1, textures[0])  # GL_TEXTURE_2D
            self.tex_parameter_i(0x0DE1, 0x2801, 0x2600)  # MIN_FILTER, NEAREST
            self.tex_parameter_i(0x0DE1, 0x2800, 0x2600)  # MAG_FILTER, NEAREST
            self.pixel_store_i(0x0CF5, 1)  # GL_UNPACK_ALIGNMENT
            self.tex_image_2d(0x0DE1, 0, 0x8058, 2, 2, 0, 0x1908, 0x1401, bytes(16))
            self.tex_sub_image_2d(0x0DE1, 0, 0, 0, 1, 1, 0x1908, 0x1401, bytes(4))
            self.generate_mipmap(0x0DE1)
            if self.get_gl_error() != 0:
                raise RuntimeError("GL query, object creation, or texture upload returned an error")
            result["gl_object_creation_succeeded"] = True
            result["gl_texture_upload_succeeded"] = True
            self.bind_texture(0x0DE1, textures[1])
            self.tex_storage_2d(0x0DE1, 2, 0x8058, 2, 2)  # GL_RGBA8, two mip levels
            if self.get_gl_error() != 0:
                raise RuntimeError("GL texture storage allocation returned an error")
            result["gl_texture_storage_succeeded"] = True
            framebuffers = self.gen_framebuffers(1)
            if not framebuffers[0]:
                raise RuntimeError("GL framebuffer creation returned a zero name")
            self.bind_framebuffer(0x8D40, framebuffers[0])  # GL_FRAMEBUFFER
            self.framebuffer_texture_2d(0x8D40, 0x8CE0, 0x0DE1, textures[0], 0)
            status = self.check_framebuffer_status(0x8D40)
            if status != 0x8CD5 or self.get_gl_error() != 0:  # GL_FRAMEBUFFER_COMPLETE
                raise RuntimeError(f"GL color framebuffer is incomplete: 0x{status:04x}")
            result["gl_color_framebuffer_complete"] = True
            renderbuffers = self.gen_renderbuffers(1)
            if not renderbuffers[0]:
                raise RuntimeError("GL renderbuffer creation returned a zero name")
            self.bind_renderbuffer(0x8D41, renderbuffers[0])  # GL_RENDERBUFFER
            self.renderbuffer_storage(0x8D41, 0x88F0, 2, 2)  # GL_DEPTH24_STENCIL8
            self.renderbuffer_storage_multisample(0x8D41, 0, 0x88F0, 2, 2)
            self.framebuffer_renderbuffer(0x8D40, 0x821A, 0x8D41, renderbuffers[0])
            status = self.check_framebuffer_status(0x8D40)
            if status != 0x8CD5 or self.get_gl_error() != 0:
                raise RuntimeError(f"GL depth-stencil framebuffer is incomplete: 0x{status:04x}")
            result["gl_renderbuffer_framebuffer_complete"] = True
            self.disable(0x0B71)  # GL_DEPTH_TEST
            self.enable(0x0C11)  # GL_SCISSOR_TEST
            self.viewport(0, 0, 2, 2)
            self.depth_range_f(0.125, 0.875)
            self.scissor(0, 0, 2, 2)
            self.depth_mask(True)
            self.blend_func(0x0302, 0x0303)  # GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA
            self.clear_color(0.25, 0.5, 0.75, 1.0)
            self.clear(0x4500)  # GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT | GL_STENCIL_BUFFER_BIT
            if self.get_gl_error() != 0:
                raise RuntimeError("GL state setup or framebuffer clear returned an error")
            result["gl_state_and_clear_succeeded"] = True
            self.read_buffer(0)  # GL_NONE
            self.draw_buffers((0x8CE0, 0, 0, 0))  # GL_COLOR_ATTACHMENT0, GL_NONE x3
            self.clear_buffer_fv(0x1800, 0, (0.0, 0.25, 0.5, 1.0))
            self.clear_buffer_fv(0x1801, 0, (1.0,))
            if self.get_gl_error() != 0:
                raise RuntimeError("GL read/draw buffer selection or clear returned an error")
            result["gl_read_draw_buffers_succeeded"] = True
            self.stencil_mask(0xFF)
            self.clear_buffer_iv(0x1802, 0, (0,))  # GL_STENCIL
            self.stencil_mask(0)
            if self.get_gl_error() != 0:
                raise RuntimeError("GL stencil mask or integer clear returned an error")
            result["gl_stencil_clear_succeeded"] = True
            buffers = self.gen_buffers(1)
            if not buffers[0]:
                raise RuntimeError("GL buffer creation returned a zero name")
            self.bind_buffer(0x8A11, buffers[0])  # GL_UNIFORM_BUFFER
            self.buffer_data(0x8A11, 16, bytes(16), 0x88E8)  # GL_DYNAMIC_DRAW
            self.buffer_sub_data(0x8A11, 4, bytes((1, 2, 3, 4)))
            self.bind_buffer_base(0x8A11, 0, buffers[0])
            if self.get_gl_error() != 0:
                raise RuntimeError("GL uniform buffer allocation or binding returned an error")
            result["gl_uniform_buffer_succeeded"] = True
            samplers = self.gen_samplers(1)
            if not samplers[0]:
                raise RuntimeError("GL sampler creation returned a zero name")
            self.sampler_parameter_i(samplers[0], 0x2801, 0x2600)  # GL_TEXTURE_MIN_FILTER, GL_NEAREST
            self.sampler_parameter_i(samplers[0], 0x2800, 0x2600)  # GL_TEXTURE_MAG_FILTER, GL_NEAREST
            self.bind_sampler(0, samplers[0])
            if self.get_gl_error() != 0:
                raise RuntimeError("GL sampler parameter or binding returned an error")
            result["gl_sampler_succeeded"] = True
            result["gl_get_string_proc_available"] = bool(self.get_proc_address("glGetString"))
            if not self.swap_buffers(display, surface):
                raise RuntimeError(f"eglSwapBuffers failed: EGL error 0x{self.get_error():04x}")
            result["pbuffer_swap_succeeded"] = True
        finally:
            if current:
                self.bind_vertex_array(0)
                self.bind_texture(0x0DE1, 0)
                self.bind_framebuffer(0x8D40, 0)
                self.bind_renderbuffer(0x8D41, 0)
                if buffers:
                    self.bind_buffer_base(0x8A11, 0, 0)
                    self.bind_buffer(0x8A11, 0)
                    self.delete_buffers(buffers)
                if samplers:
                    self.bind_sampler(0, 0)
                    self.delete_samplers(samplers)
                if vertex_arrays:
                    self.delete_vertex_arrays(vertex_arrays)
                if textures:
                    self.delete_textures(textures)
                if framebuffers:
                    self.delete_framebuffers(framebuffers)
                if renderbuffers:
                    self.delete_renderbuffers(renderbuffers)
                cleanup["gl_objects"] = self.get_gl_error() == 0
            if context:
                cleanup["unbind"] = self.make_current(display, 0, 0)
                cleanup["context"] = self.destroy_context(display, context)
            if surface:
                cleanup["surface"] = self.destroy_surface(display, surface)
            if initialized:
                cleanup["display"] = self.terminate(display)
        if not all(cleanup.values()):
            raise RuntimeError(f"ANGLE cleanup failed: {cleanup}")
        result["cleanup"] = cleanup
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a local Windows ANGLE EGL/GLES pair offscreen")
    parser.add_argument("--dll-directory", type=Path, required=True)
    args = parser.parse_args()
    with WindowsAngleBackend(args.dll_directory) as backend:
        print(json.dumps(backend.self_check(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
