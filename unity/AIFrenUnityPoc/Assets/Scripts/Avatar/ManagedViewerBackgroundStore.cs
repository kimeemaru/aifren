using System;
using System.IO;
using System.Security.Cryptography;
using UnityEngine;

namespace AIFren.UnityPoc.Avatar
{
    internal static class ManagedViewerBackgroundStore
    {
        internal static bool TryImport(string sourcePath, out string managedPath, out string error)
        {
            managedPath = string.Empty; error = string.Empty;
            try
            {
                string extension = Path.GetExtension(sourcePath).ToLowerInvariant();
                if (extension != ".png" && extension != ".jpg" && extension != ".jpeg") throw new InvalidOperationException("Choose a PNG or JPEG image.");
                byte[] bytes = File.ReadAllBytes(sourcePath);
                using (SHA256 hash = SHA256.Create())
                {
                    string name = BitConverter.ToString(hash.ComputeHash(bytes)).Replace("-", string.Empty).ToLowerInvariant() + extension;
                    string directory = Path.Combine(Application.persistentDataPath, "AIFren", "ViewerBackgrounds");
                    Directory.CreateDirectory(directory);
                    managedPath = Path.Combine(directory, name);
                    if (!File.Exists(managedPath)) File.WriteAllBytes(managedPath, bytes);
                }
                return true;
            }
            catch (Exception exception) { error = exception.Message; return false; }
        }
    }
}
