using System;
using System.Runtime.InteropServices;
using UnityEngine;

namespace AIFren.UnityPoc.UI
{
    /// <summary>
    /// Best-effort EWMH always-on-top support for a Linux X11 player. Unity
    /// does not expose this window-manager state, so send the standard EWMH
    /// request directly instead of depending on an optional desktop utility.
    /// </summary>
    public static class LinuxWindowAlwaysOnTop
    {
        private const int ClientMessage = 33;
        private const long SubstructureNotifyMask = 1L << 19;
        private const long SubstructureRedirectMask = 1L << 20;
        private const int AddState = 1;
        private const int RemoveState = 0;

        [StructLayout(LayoutKind.Sequential)]
        private struct XClientMessageEvent
        {
            public int type;
            public IntPtr serial;
            public int send_event;
            public IntPtr display;
            public IntPtr window;
            public IntPtr message_type;
            public int format;
            public IntPtr data0;
            public IntPtr data1;
            public IntPtr data2;
            public IntPtr data3;
            public IntPtr data4;
        }

        [DllImport("libX11.so.6")]
        private static extern IntPtr XOpenDisplay(IntPtr displayName);

        [DllImport("libX11.so.6")]
        private static extern int XCloseDisplay(IntPtr display);

        [DllImport("libX11.so.6")]
        private static extern IntPtr XDefaultRootWindow(IntPtr display);

        [DllImport("libX11.so.6")]
        private static extern void XGetInputFocus(IntPtr display, out IntPtr focus, out int revertTo);

        [DllImport("libX11.so.6")]
        private static extern IntPtr XInternAtom(IntPtr display, string atomName, bool onlyIfExists);

        [DllImport("libX11.so.6")]
        private static extern int XSendEvent(
            IntPtr display,
            IntPtr window,
            bool propagate,
            IntPtr eventMask,
            ref XClientMessageEvent sendEvent);

        [DllImport("libX11.so.6")]
        private static extern int XFlush(IntPtr display);

        [DllImport("libX11.so.6")]
        private static extern int XGetGeometry(IntPtr display, IntPtr drawable, out IntPtr root, out int x, out int y,
            out uint width, out uint height, out uint borderWidth, out uint depth);

        [DllImport("libX11.so.6")]
        private static extern int XTranslateCoordinates(IntPtr display, IntPtr source, IntPtr destination,
            int sourceX, int sourceY, out int destinationX, out int destinationY, out IntPtr child);

        public static bool TryGetFocusedWindowGeometry(out string geometry)
        {
            geometry = "unavailable";
            if (!IsX11Session(Environment.GetEnvironmentVariable("DISPLAY"), Environment.GetEnvironmentVariable("XDG_SESSION_TYPE"))) return false;
            IntPtr display = IntPtr.Zero;
            try
            {
                display = XOpenDisplay(IntPtr.Zero);
                if (display == IntPtr.Zero) return false;
                XGetInputFocus(display, out IntPtr window, out _);
                if (window == IntPtr.Zero || window == new IntPtr(1)) return false;
                if (XGetGeometry(display, window, out IntPtr root, out _, out _, out uint width, out uint height, out uint border, out _) == 0) return false;
                if (XTranslateCoordinates(display, window, root, 0, 0, out int x, out int y, out _) == 0) return false;
                geometry = x + "," + y + " " + width + "x" + height + " border=" + border;
                return true;
            }
            catch { return false; }
            finally { if (display != IntPtr.Zero) XCloseDisplay(display); }
        }

        public static bool TrySet(bool enabled, out string detail)
        {
            if (Application.platform != RuntimePlatform.LinuxPlayer)
            {
                detail = "Always on Top is currently supported on Linux X11 players only.";
                return false;
            }

            if (!IsX11Session(Environment.GetEnvironmentVariable("DISPLAY"),
                Environment.GetEnvironmentVariable("XDG_SESSION_TYPE")))
            {
                detail = "Always on Top requires an X11/EWMH desktop session.";
                return false;
            }

            IntPtr display = IntPtr.Zero;
            try
            {
                display = XOpenDisplay(IntPtr.Zero);
                if (display == IntPtr.Zero)
                {
                    detail = "Always on Top could not open the X11 display.";
                    return false;
                }

                XGetInputFocus(display, out IntPtr playerWindow, out _);
                // None and PointerRoot are not application windows.
                if (playerWindow == IntPtr.Zero || playerWindow == new IntPtr(1))
                {
                    detail = "Always on Top could not identify the focused AIFren player window.";
                    return false;
                }

                IntPtr root = XDefaultRootWindow(display);
                IntPtr state = XInternAtom(display, "_NET_WM_STATE", false);
                IntPtr above = XInternAtom(display, "_NET_WM_STATE_ABOVE", false);
                if (root == IntPtr.Zero || state == IntPtr.Zero || above == IntPtr.Zero)
                {
                    detail = "Always on Top is not supported by this X11 window manager.";
                    return false;
                }

                XClientMessageEvent request = new XClientMessageEvent
                {
                    type = ClientMessage,
                    send_event = 1,
                    display = display,
                    window = playerWindow,
                    message_type = state,
                    format = 32,
                    data0 = new IntPtr(enabled ? AddState : RemoveState),
                    data1 = above,
                    data2 = IntPtr.Zero,
                    data3 = new IntPtr(1), // EWMH source indication: application.
                    data4 = IntPtr.Zero,
                };
                IntPtr mask = new IntPtr(SubstructureNotifyMask | SubstructureRedirectMask);
                if (XSendEvent(display, root, false, mask, ref request) == 0)
                {
                    detail = "Always on Top request was rejected by this X11 window manager.";
                    return false;
                }

                XFlush(display);
                detail = enabled ? "Always on Top enabled." : "Always on Top disabled.";
                return true;
            }
            catch (Exception exception)
            {
                detail = "Always on Top is unavailable: " + exception.Message;
                return false;
            }
            finally
            {
                if (display != IntPtr.Zero) XCloseDisplay(display);
            }
        }

        /// <summary>
        /// Requests the EWMH fullscreen state for the focused Unity player.
        /// Unlike a maximized/work-area window, fullscreen covers panels and
        /// titlebar space on X11 window managers.
        /// </summary>
        public static bool TrySetFullscreen(bool enabled, out string detail)
        {
            if (Application.platform != RuntimePlatform.LinuxPlayer ||
                !IsX11Session(Environment.GetEnvironmentVariable("DISPLAY"), Environment.GetEnvironmentVariable("XDG_SESSION_TYPE")))
            {
                detail = "Native fullscreen is unavailable outside an X11 player session.";
                return false;
            }
            IntPtr display = IntPtr.Zero;
            try
            {
                display = XOpenDisplay(IntPtr.Zero);
                if (display == IntPtr.Zero) { detail = "Could not open the X11 display."; return false; }
                XGetInputFocus(display, out IntPtr playerWindow, out _);
                if (playerWindow == IntPtr.Zero || playerWindow == new IntPtr(1)) { detail = "Could not identify the AIFren player window."; return false; }
                IntPtr root = XDefaultRootWindow(display);
                IntPtr state = XInternAtom(display, "_NET_WM_STATE", false);
                IntPtr fullscreen = XInternAtom(display, "_NET_WM_STATE_FULLSCREEN", false);
                if (root == IntPtr.Zero || state == IntPtr.Zero || fullscreen == IntPtr.Zero) { detail = "This X11 window manager does not expose EWMH fullscreen."; return false; }
                XClientMessageEvent request = new XClientMessageEvent
                {
                    type = ClientMessage, send_event = 1, display = display, window = playerWindow, message_type = state, format = 32,
                    data0 = new IntPtr(enabled ? AddState : RemoveState), data1 = fullscreen, data3 = new IntPtr(1),
                };
                if (XSendEvent(display, root, false, new IntPtr(SubstructureNotifyMask | SubstructureRedirectMask), ref request) == 0)
                {
                    detail = "The X11 window manager rejected the fullscreen request."; return false;
                }
                XFlush(display); detail = enabled ? "Native X11 fullscreen requested." : "Native X11 fullscreen cleared."; return true;
            }
            catch (Exception exception) { detail = "Native fullscreen is unavailable: " + exception.Message; return false; }
            finally { if (display != IntPtr.Zero) XCloseDisplay(display); }
        }

        public static bool IsX11Session(string display, string sessionType)
        {
            return !string.IsNullOrWhiteSpace(display) &&
                !string.Equals(sessionType, "wayland", StringComparison.OrdinalIgnoreCase);
        }
    }
}
