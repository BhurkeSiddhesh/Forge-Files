#!/usr/bin/env python3
"""
PDF Password Remover
Removes password protection from PDF files.
"""

import argparse
import sys
from pathlib import Path


def remove_pdf_password(input_path: str, password: str, output_path: str = None) -> str:
    """
    Remove password protection from a PDF file.
    
    Args:
        input_path: Path to the password-protected PDF file
        password: The password to unlock the PDF
        output_path: Path for the output file (optional, defaults to input_unlocked.pdf)
    
    Returns:
        Path to the output file
    
    Raises:
        FileNotFoundError: If input file doesn't exist
        pikepdf.PasswordError: If password is incorrect
    """
    input_file = Path(input_path)
    
    if not input_file.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")
    
    if not input_file.suffix.lower() == '.pdf':
        raise ValueError(f"Input file must be a PDF: {input_path}")
    
    # Generate output path if not provided
    if output_path is None:
        output_file = input_file.parent / f"{input_file.stem}_unlocked.pdf"
    else:
        output_file = Path(output_path)
    
    try:
        import pikepdf
    except ImportError:
        # Let the caller handle this or fail with standard ImportError
        raise

    # Open the PDF with the supplied password and save without encryption.
    # Owner-restricted PDFs (permission restrictions, no open password) open
    # with an *empty* password, so a wrong/irrelevant guess is retried once
    # with "" before giving up — matching scripts.pdf_utils.remove_pdf_password
    # (the app's own unlock path) and mainstream "Unlock PDF" tools. A PDF with
    # a genuine open (user) password still fails on a wrong password, since the
    # empty-password retry can't open it either, so this never false-unlocks.
    try:
        pdf = pikepdf.open(input_file, password=password)
    except pikepdf.PasswordError:
        try:
            pdf = pikepdf.open(input_file, password="")
        except pikepdf.PasswordError:
            raise

    with pdf:
        pdf.save(output_file)

    return str(output_file)


def main():
    parser = argparse.ArgumentParser(
        description="Remove password protection from a PDF file",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python pdf_password_remover.py document.pdf mypassword
  python pdf_password_remover.py document.pdf mypassword -o unlocked.pdf
  python pdf_password_remover.py /path/to/directory mypassword
        """
    )
    
    parser.add_argument(
        "input_pdf",
        nargs='?',
        help="Path to the PDF file or directory"
    )
    
    parser.add_argument(
        "password",
        nargs='?',
        help="Password to unlock the PDF"
    )
    
    parser.add_argument(
        "-o", "--output",
        help="Output file path (for single file) or directory (for batch)",
        default=None
    )
    
    args = parser.parse_args()
    
    # Interactive input if positional arguments are missing
    input_pdf = args.input_pdf
    password = args.password
    
    if not input_pdf:
        input_pdf = input("Enter path to PDF file or directory: ").strip().strip('"')
    
    if not password:
        password = input("Enter password: ").strip()

    input_path_obj = Path(input_pdf)

    if input_path_obj.is_dir():
        # Directory Batch Processing
        print(f"Processing directory: {input_path_obj}")
        pdf_files = sorted(list(input_path_obj.glob("*.pdf")))

        if not pdf_files:
            print("No PDF files found in the directory.")
            sys.exit(0)

        success_count = 0
        fail_count = 0

        # Handle output directory logic
        output_dir = None
        if args.output:
            output_dir = Path(args.output)
            output_dir.mkdir(parents=True, exist_ok=True)

        for pdf_file in pdf_files:
            try:
                # Determine output path for this file
                file_output_path = None
                if output_dir:
                    file_output_path = output_dir / pdf_file.name

                # Process the file
                out_path = remove_pdf_password(str(pdf_file), password, str(file_output_path) if file_output_path else None)
                print(f"✓ {pdf_file.name}")
                success_count += 1
            except Exception as e:
                # Check if it's a PasswordError (handle deferred import)
                is_password_error = False
                if type(e).__name__ == 'PasswordError':
                    try:
                        import pikepdf
                        if isinstance(e, pikepdf.PasswordError):
                            is_password_error = True
                    except ImportError:
                        pass

                if is_password_error:
                    print(f"✗ {pdf_file.name}: Incorrect password")
                else:
                    print(f"✗ {pdf_file.name}: Error: {e}")
                fail_count += 1

        print(f"\nBatch processing complete.")
        print(f"Successful: {success_count}")
        print(f"Failed: {fail_count}")

    else:
        # Single File Processing
        try:
            output_path = remove_pdf_password(input_pdf, password, args.output)
            print(f"✓ Password removed successfully!")
            print(f"  Output: {output_path}")
        except FileNotFoundError as e:
            print(f"✗ Error: {e}")
            sys.exit(1)
        except ImportError as e:
            if e.name == 'pikepdf':
                print("Error: pikepdf is not installed.")
                print("Install it with: pip install pikepdf")
                sys.exit(1)
            raise
        except Exception as e:
            # Check if it's a PasswordError (handle deferred import)
            is_password_error = False
            if type(e).__name__ == 'PasswordError':
                try:
                    import pikepdf
                    if isinstance(e, pikepdf.PasswordError):
                        is_password_error = True
                except ImportError:
                    pass

            if is_password_error:
                print(f"✗ Error: Incorrect password")
                sys.exit(1)

            print(f"✗ Error: {e}")
            sys.exit(1)


if __name__ == "__main__":  # pragma: no cover
    main()

