using System;
using System.IO;
using UniGLTF;
using UniJSON;

namespace AIFren.UnityPoc.Avatar
{
    internal enum AvatarModelFormat
    {
        Unsupported,
        Vrm0,
        Vrm1,
    }

    /// <summary>Recognizes VRM avatars from their glTF metadata, not their filename alone.</summary>
    internal static class AvatarModelFormatClassifier
    {
        private const string Vrm0Extension = "VRM";
        private const string Vrm1Extension = "VRMC_vrm";

        internal static bool TryClassify(string path, out AvatarModelFormat format, out string error)
        {
            format = AvatarModelFormat.Unsupported;
            error = string.Empty;
            string extension = Path.GetExtension(path);
            if (string.IsNullOrWhiteSpace(path) || !File.Exists(path) ||
                (!string.Equals(extension, ".vrm", StringComparison.OrdinalIgnoreCase) &&
                 !string.Equals(extension, ".glb", StringComparison.OrdinalIgnoreCase)))
            {
                error = "Choose a .vrm or VRM-compatible .glb avatar file.";
                return false;
            }

            try
            {
                using var data = new GlbLowLevelParser(path, File.ReadAllBytes(path)).Parse();
                if (!(data.GLTF.extensions is glTFExtensionImport extensions))
                {
                    error = "This GLB does not contain VRM avatar metadata.";
                    return false;
                }

                bool hasVrm0 = false;
                bool hasVrm1 = false;
                foreach (var item in extensions.ObjectItems())
                {
                    string name = item.Key.GetString();
                    hasVrm0 |= string.Equals(name, Vrm0Extension, StringComparison.Ordinal);
                    hasVrm1 |= string.Equals(name, Vrm1Extension, StringComparison.Ordinal);
                }

                if (hasVrm1)
                {
                    format = AvatarModelFormat.Vrm1;
                    return true;
                }
                if (hasVrm0)
                {
                    format = AvatarModelFormat.Vrm0;
                    return true;
                }

                error = "This GLB does not contain VRM avatar metadata.";
                return false;
            }
            catch (Exception)
            {
                error = "This file is not a readable VRM avatar container.";
                return false;
            }
        }

        internal static bool IsSupportedAvatarFile(string path) => TryClassify(path, out _, out _);
    }
}
