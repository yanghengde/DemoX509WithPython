# The certificate private key stays in the Windows certificate store.
# Input and output use stdin/stdout; never write the JWT to disk or logs.
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$taskStore = $null
$taskRsa = $null
try {
    $taskInput = [Console]::In.ReadToEnd() | ConvertFrom-Json
    if ($taskInput.store_location -notin @('LocalMachine', 'CurrentUser')) { throw 'Invalid store location.' }
    if ($taskInput.thumbprint -notmatch '^[A-Fa-f0-9]{40}$') { throw 'A SHA-1 certificate thumbprint is required.' }
    $taskLifetime = [int]$taskInput.lifetime_seconds
    if ($taskLifetime -lt 30 -or $taskLifetime -gt 120) { throw 'Token lifetime must be 30 to 120 seconds.' }
    $taskLocation = [System.Security.Cryptography.X509Certificates.StoreLocation]::$($taskInput.store_location)
    $taskStore = New-Object System.Security.Cryptography.X509Certificates.X509Store('My', $taskLocation)
    $taskStore.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadOnly)
    $taskMatches = $taskStore.Certificates.Find(
        [System.Security.Cryptography.X509Certificates.X509FindType]::FindByThumbprint,
        $taskInput.thumbprint, $false)
    if ($taskMatches.Count -ne 1) { throw 'Certificate not found or not unique in the selected store.' }
    $taskCert = $taskMatches[0]
    $taskNow = [DateTimeOffset]::UtcNow
    if (-not $taskCert.HasPrivateKey) { throw 'Certificate has no accessible private key.' }
    if ($taskCert.NotBefore.ToUniversalTime() -gt $taskNow.UtcDateTime -or
        $taskCert.NotAfter.ToUniversalTime() -le $taskNow.AddSeconds($taskLifetime).UtcDateTime) {
        throw 'Certificate is outside its validity period.'
    }
    $taskRsa = [System.Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPrivateKey($taskCert)
    if ($null -eq $taskRsa -or $taskRsa.KeySize -lt 2048) { throw 'An RSA private key of at least 2048 bits is required.' }
    function ConvertTo-Base64Url([byte[]] $Value) {
        return [Convert]::ToBase64String($Value).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    }
    $taskHeader = @{
        alg = 'RS256'; typ = 'JWT'; kid = $taskCert.Thumbprint
        x5t = (ConvertTo-Base64Url $taskCert.GetCertHash())
    }
    # These are JwtSecurityTokenHandler's outbound mappings for Name/Thumbprint.
    $taskClaims = @{
        unique_name = $taskCert.Issuer
        certthumbprint = $taskCert.Thumbprint
        'urn:realm' = 'x.509'
        iss = [string]$taskInput.issuer
        aud = [string]$taskInput.audience
        iat = $taskNow.ToUnixTimeSeconds()
        nbf = $taskNow.ToUnixTimeSeconds()
        exp = $taskNow.AddSeconds($taskLifetime).ToUnixTimeSeconds()
    }
    $taskUnsigned = (ConvertTo-Base64Url ([Text.Encoding]::UTF8.GetBytes(($taskHeader | ConvertTo-Json -Compress)))) + '.' +
                    (ConvertTo-Base64Url ([Text.Encoding]::UTF8.GetBytes(($taskClaims | ConvertTo-Json -Compress))))
    $taskSignature = $taskRsa.SignData([Text.Encoding]::UTF8.GetBytes($taskUnsigned),
        [Security.Cryptography.HashAlgorithmName]::SHA256, [Security.Cryptography.RSASignaturePadding]::Pkcs1)
    $taskToken = $taskUnsigned + '.' + (ConvertTo-Base64Url $taskSignature)
    @{token=$taskToken; expires_at=$taskClaims.exp} | ConvertTo-Json -Compress
} catch {
    # Safe diagnostic only: no token, password or private material in errors.
    [Console]::Error.WriteLine('Windows certificate signing failed: ' + $_.Exception.Message)
    exit 1
} finally {
    if ($null -ne $taskRsa) { $taskRsa.Dispose() }
    if ($null -ne $taskStore) { $taskStore.Close() }
}
