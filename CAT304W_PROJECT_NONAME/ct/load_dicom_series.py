import importlib.util
import subprocess
import sys
from pathlib import Path


REQUIRED_PACKAGES = ["pydicom", "SimpleITK", "matplotlib", "numpy"]
DICOM_DIR = Path(
    r"E:\肺部ct影像\manifest-1600709154662\LIDC-IDRI\LIDC-IDRI-0001\01-01-2000-NA-NA-30178\3000566.000000-NA-03192"
)


def ensure_packages(packages: list[str]) -> None:
    missing = [pkg for pkg in packages if importlib.util.find_spec(pkg) is None]
    if not missing:
        print("All required packages already installed.")
        return

    print(f"Installing missing packages: {', '.join(missing)}")
    subprocess.check_call([sys.executable, "-m", "pip", "install", *missing])


def sort_key(ds) -> float:
    """Sorting by ImagePositionPatient[2] when present, otherwise InstanceNumber."""
    ipp = getattr(ds, "ImagePositionPatient", None)
    if ipp and len(ipp) >= 3:
        try:
            return float(ipp[2])
        except (TypeError, ValueError):
            pass
    return float(getattr(ds, "InstanceNumber", 0))


def main() -> None:
    ensure_packages(REQUIRED_PACKAGES)

    import pydicom
    import SimpleITK  # noqa: F401 - ensure SimpleITK is present even though not used directly
    import matplotlib.pyplot as plt
    import numpy as np

    if not DICOM_DIR.exists():
        raise FileNotFoundError(f"DICOM directory not found: {DICOM_DIR}")

    dicom_files = sorted([p for p in DICOM_DIR.glob("*.dcm") if p.is_file()])
    if not dicom_files:
        raise RuntimeError(f"No DICOM files found in {DICOM_DIR}")

    datasets = [pydicom.dcmread(path) for path in dicom_files]
    datasets.sort(key=sort_key)

    hu_slices = []
    for ds in datasets:
        pixel_array = ds.pixel_array.astype(np.float32)
        slope = float(getattr(ds, "RescaleSlope", 1))
        intercept = float(getattr(ds, "RescaleIntercept", 0))
        hu_slices.append(pixel_array * slope + intercept)

    volume_hu = np.stack(hu_slices, axis=0)

    print(f"Volume shape (slices, height, width): {volume_hu.shape}")
    print(f"HU range: {volume_hu.min()} to {volume_hu.max()}")

    middle_idx = volume_hu.shape[0] // 2
    plt.imshow(volume_hu[middle_idx], cmap="gray")
    plt.title(f"Middle slice (index {middle_idx + 1})")
    plt.axis("off")
    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()
