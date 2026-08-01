# Web Attack Playbooks

## Injection And Command Execution

Identify the parameter/body location and payload family, then inspect response behavior, application errors, command output, callback, new process, or state change. Status `200` alone is not success.

## File And Path Operations

- Traversal/read: require returned sensitive content or a corroborating server-side event for success.
- Upload: distinguish transfer acceptance, stored file, executable placement, later request, execution, and persistence.
- Download: distinguish a URL or filename from confirmed response content and endpoint write.
- Webshell: require a chain such as upload/presence plus execution, command echo, process evidence, or callback.

## Authentication Attacks

Separate scan, failed attempts, password spray, credential stuffing, successful login, unusual session creation, and post-login action. Consider source breadth, account breadth, rate, and returned identity evidence.

## Information Exposure And Misconfiguration

For directory listing, API documentation, debug endpoint, source-control metadata, configuration, or sensitive-file exposure, distinguish a requested path from returned sensitive content. Generic success text or status codes do not prove disclosure. Identify the concrete returned content and affected service before claiming impact.

## XXE And Client-Side Injection

- XXE: identify XML parser input and entity behavior, then look for returned file content, out-of-band callback, parser error, or server-side access evidence.
- XSS: distinguish reflected payload text, browser-executable context, stored retrieval, and actual client execution. A payload echoed in escaped text is not successful XSS.

## Tool And Scanner Signatures

Exploit-framework, scanner, scripted user-agent, and payload signatures support an attack or test hypothesis. Authorization must come from scoped governed context; a hardcoded hostname, path, user agent, or source range must not auto-suppress the alert.

## Proxy And Target Attribution

Keep client candidate, forwarded chain, CDN/reverse proxy/load balancer, protected service, business owner, and response target separate. Use trusted topology or asset evidence to resolve them; never block shared infrastructure by field position alone.
