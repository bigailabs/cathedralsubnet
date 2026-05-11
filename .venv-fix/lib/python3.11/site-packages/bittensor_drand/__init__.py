from typing import Union, Optional

from bittensor_drand.bittensor_drand import (
    get_encrypted_commit as _get_encrypted_commit,
    get_encrypted_commitment as _get_encrypted_commitment,
    encrypt as _encrypt,
    encrypt_at_round as _encrypt_at_round,
    decrypt as _decrypt,
    decrypt_with_signature as _decrypt_with_signature,
    get_signature_for_round as _get_signature_for_round,
    get_latest_round as _get_latest_round,
    encrypt_mlkem768 as _encrypt_mlkem768,
    mlkem_kdf_id as _mlkem_kdf_id,
)


def get_encrypted_commit(
    uids: list[int],
    weights: list[int],
    version_key: int,
    tempo: int,
    current_block: int,
    netuid: int,
    subnet_reveal_period_epochs: int,
    block_time: Union[int, float],
    hotkey: bytes,
) -> tuple[bytes, int]:
    """Returns encrypted commit and target round for `commit_crv3_weights` extrinsic.

    Arguments:
        uids: The uids to commit.
        weights: The weights associated with the uids.
        version_key: The version key to use for committing and revealing. Default is `bittensor.core.settings.version_as_int`.
        tempo: Number of blocks in one epoch.
        current_block: The current block number in the network.
        netuid: The network unique identifier (NetUID) for the subnet.
        subnet_reveal_period_epochs: Number of epochs after which the reveal will be performed. Corresponds to the hyperparameter `commit_reveal_weights_interval` of the subnet. In epochs.
        block_time: Amount of time in seconds for one block. Defaults to 12 seconds.
        hotkey: The hotkey of a neuron-committer is represented as public_key bytes (wallet.hotkey.public_key).

    Returns:
        commit (bytes): Raw bytes of the encrypted and compressed uids & weights values for setting weights.
        target_round (int): Drand round number when weights have to be revealed. Based on Drand Quicknet network.

    Raises:
        ValueError: If the input parameters are invalid or encryption fails.
    """
    return _get_encrypted_commit(
        uids,
        weights,
        version_key,
        tempo,
        current_block,
        netuid,
        subnet_reveal_period_epochs,
        block_time,
        hotkey,
    )


def get_encrypted_commitment(
    data: str, blocks_until_reveal: int, block_time: Union[int, float] = 12.0
) -> tuple[bytes, int]:
    """Encrypts arbitrary string data with time-lock encryption.

    Arguments:
        data: The string data to encrypt.
        blocks_until_reveal: Number of blocks until the data should be revealed.
        block_time: Amount of time in seconds for one block. Defaults to 12 seconds.

    Returns:
        encrypted_data (bytes): Raw bytes of the encrypted data.
        target_round (int): Drand round number when data can be revealed.

    Raises:
        ValueError: If encryption fails.
    """
    return _get_encrypted_commitment(data, blocks_until_reveal, block_time)


def encrypt(
    data: bytes, n_blocks: int, block_time: Union[int, float] = 12.0
) -> tuple[bytes, int]:
    """Encrypts arbitrary binary data with time-lock encryption.

    Arguments:
        data: The binary data to encrypt.
        n_blocks: Number of blocks until the data should be revealed.
        block_time: Amount of time in seconds for one block. Defaults to 12 seconds.

    Returns:
        encrypted_data (bytes): Raw bytes of the encrypted data.
        target_round (int): Drand round number when data can be revealed.

    Raises:
        ValueError: If encryption fails.
    """
    return _encrypt(data, n_blocks, block_time)


def encrypt_at_round(data: bytes, reveal_round: int) -> tuple[bytes, int]:
    """Encrypts arbitrary binary data for a specific Drand reveal round.

    Arguments:
        data: The binary data to encrypt.
        reveal_round: The specific Drand round number when decryption becomes possible.

    Returns:
        encrypted_data (bytes): Raw bytes of the encrypted data.
        reveal_round (int): The Drand round number when data can be revealed (same as input).

    Raises:
        ValueError: If encryption fails.
    """
    return _encrypt_at_round(data, reveal_round)


def decrypt(encrypted_data: bytes, no_errors: bool = True) -> Optional[bytes]:
    """Decrypts previously encrypted data if the reveal time has been reached.

    Arguments:
        encrypted_data: The encrypted data to decrypt.
        no_errors: If True, returns None instead of raising exceptions when decryption fails.
                  If False, raises exceptions on decryption failures.

    Returns:
        decrypted_data (Optional[bytes]): The decrypted data if successful, None otherwise.

    Raises:
        ValueError: If decryption fails and no_errors is False.
    """
    return _decrypt(encrypted_data, no_errors)


def decrypt_with_signature(encrypted_data: bytes, signature_hex: str) -> bytes:
    """Decrypts data using a provided Drand signature.
    This function is useful when decrypting multiple ciphertexts for the same round,
    allowing you to fetch the signature once and reuse it, avoiding redundant API calls.

    Arguments:
        encrypted_data: The encrypted data to decrypt.
        signature_hex: Hex-encoded Drand BLS signature for the reveal round.

    Returns:
        decrypted_data (bytes): The decrypted data.

    Raises:
        ValueError: If decryption fails or signature is invalid.
    """
    return _decrypt_with_signature(encrypted_data, signature_hex)


def get_signature_for_round(reveal_round: int) -> str:
    """Fetches the Drand signature for a specific round.
    This is useful for batch decryption scenarios where you want to decrypt
    multiple ciphertexts for the same round without making redundant API calls.

    Arguments:
        reveal_round: The Drand round number to fetch the signature for.

    Returns:
        signature_hex (str): Hex-encoded BLS signature for the round.

    Raises:
        ValueError: If the signature cannot be fetched or is not yet available.
    """
    return _get_signature_for_round(reveal_round)


def get_latest_round() -> int:
    """Gets the latest revealed Drand round number.

    Returns:
        round (int): The latest revealed Drand round number.

    Raises:
        ValueError: If fetching the latest round fails.
    """
    return _get_latest_round()


def encrypt_mlkem768(pk_bytes: bytes, plaintext: bytes, include_key_hash: bool = False) -> bytes:
    """Encrypts data using ML-KEM-768 + XChaCha20Poly1305.

    This function encrypts plaintext using ML-KEM-768 key encapsulation followed by XChaCha20Poly1305 authenticated
    encryption. The public key is rotated every block and can be queried from the NextKey storage item.

    Blob format (include_key_hash=False): [u16 kem_len LE][kem_ct][nonce24][aead_ct]
    Blob format (include_key_hash=True):  [key_hash(16)][u16 kem_len LE][kem_ct][nonce24][aead_ct]

    Arguments:
        pk_bytes: ML-KEM-768 public key bytes (from NextKey storage, 1184 bytes)
        plaintext: Data to encrypt.
        include_key_hash: If True, prepends the twox_128 hash of pk_bytes (16 bytes) to the output.
            Required for the MEV Shield wire format (pallet-shield v2).

    Returns:
        bytes: Encrypted blob

    Raises:
        ValueError: If encryption fails (invalid public key, buffer too small, etc.)
    """
    return _encrypt_mlkem768(pk_bytes, plaintext, include_key_hash)


def mlkem_kdf_id() -> bytes:
    """Returns the KDF identifier used by ML-KEM encryption.

    This function returns the KDF (Key Derivation Function) identifier "v1", which indicates that the AEAD key is
    derived directly from the ML-KEM shared secret without any additional HKDF or hashing steps.

    The "v1" KDF means:
        - AEAD key = raw ML-KEM shared secret (32 bytes)
        - No HKDF or additional hashing applied
        - AAD (Additional Authenticated Data) = empty

    This identifier is used to verify compatibility between the encryption library and the decryption logic on the
    blockchain node.

    Returns:
        bytes: KDF identifier (b"v1")
    """
    return _mlkem_kdf_id()
