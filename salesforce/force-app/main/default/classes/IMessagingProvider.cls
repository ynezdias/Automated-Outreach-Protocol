/**
 * Messaging-vendor abstraction. The send path and inbound endpoints depend on
 * this interface only; everything vendor-specific (endpoints, payload shapes,
 * webhook validation) lives inside one implementation class. Swapping vendors
 * means writing one new implementation — never touching the send gates.
 */
public interface IMessagingProvider {
    ProviderResult send(String toNumber, String body, Id leadId);
    Boolean validateInbound(RestRequest request);
}
