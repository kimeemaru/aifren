using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using AIFren.UnityPoc.Avatar;
using AIFren.UnityPoc.UI;
using NUnit.Framework;
using UnityEngine;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class AssetLibraryGridTests
    {
        [Test]
        public void ModelImportContainerCheckRejectsTextAndImageFiles()
        {
            string root = Path.Combine(Application.temporaryCachePath, "AIFrenAssetValidationTests");
            Directory.CreateDirectory(root);
            string valid = Path.Combine(root, Guid.NewGuid() + ".vrm");
            string image = Path.Combine(root, Guid.NewGuid() + ".jpg");
            string text = Path.Combine(root, Guid.NewGuid() + ".txt");
            File.WriteAllBytes(valid, new byte[] { 0x67, 0x6c, 0x54, 0x46, 2, 0, 0, 0, 20, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0 });
            File.WriteAllBytes(image, new byte[] { 0xff, 0xd8, 0xff, 0xe0 });
            File.WriteAllText(text, "not a model");
            MethodInfo validVrm = typeof(AvatarLoader).Assembly.GetType("AIFren.UnityPoc.Avatar.ManagedAssetLibrary")
                .GetMethod("IsValidVrmFile", BindingFlags.Static | BindingFlags.NonPublic | BindingFlags.Public);
            try
            {
                Assert.IsTrue((bool)validVrm.Invoke(null, new object[] { valid }));
                Assert.IsFalse((bool)validVrm.Invoke(null, new object[] { image }));
                Assert.IsFalse((bool)validVrm.Invoke(null, new object[] { text }));
            }
            finally { File.Delete(valid); File.Delete(image); File.Delete(text); }
        }

        [Test]
        public void RebuildClearRemovesEveryExistingTileBeforeNewTilesAreAdded()
        {
            GameObject content = new GameObject("Asset library test content", typeof(RectTransform));
            var oldTiles = new List<GameObject>();
            for (int index = 0; index < 7; index++)
            {
                GameObject tile = new GameObject("Old tile " + index, typeof(RectTransform));
                tile.transform.SetParent(content.transform, false);
                oldTiles.Add(tile);
            }

            MethodInfo clear = typeof(AIFrenPocController).GetMethod(
                "ClearLibraryTiles", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.NotNull(clear);
            clear.Invoke(null, new object[] { content.transform });

            Assert.AreEqual(0, content.transform.childCount);
            foreach (GameObject tile in oldTiles) UnityEngine.Object.DestroyImmediate(tile);
            UnityEngine.Object.DestroyImmediate(content);
        }
    }
}
