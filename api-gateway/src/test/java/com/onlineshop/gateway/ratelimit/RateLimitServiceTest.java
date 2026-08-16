package com.onlineshop.gateway.ratelimit;

import java.time.Duration;
import java.util.List;
import java.util.function.Supplier;

import io.github.bucket4j.Bandwidth;
import io.github.bucket4j.BucketConfiguration;
import io.github.bucket4j.distributed.BucketProxy;
import io.github.bucket4j.distributed.proxy.ProxyManager;
import io.github.bucket4j.distributed.proxy.RemoteBucketBuilder;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.mock.web.MockHttpServletRequest;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class RateLimitServiceTest {

    private static final RateLimitConfigProperties CONFIG = new RateLimitConfigProperties(
            new RateLimitConfigProperties.LimitSettings(3, 2),
            new RateLimitConfigProperties.LimitSettings(5, 4),
            List.of());

    private RateLimitService buildService(RemoteBucketBuilder<String> remoteBuilder) {
        ProxyManager<String> proxyManager = mock(ProxyManager.class);
        when(proxyManager.builder()).thenReturn(remoteBuilder);
        return new RateLimitService(proxyManager, CONFIG);
    }

    private RemoteBucketBuilder<String> bucketBuilderReturning(BucketProxy bucket) {
        RemoteBucketBuilder<String> remoteBuilder = mock(RemoteBucketBuilder.class);
        when(remoteBuilder.build(anyString(), any(Supplier.class))).thenReturn(bucket);
        return remoteBuilder;
    }

    @Test
    void anonymousBucketUsesBurstCapacityAndPerMinuteRefill() {
        RemoteBucketBuilder<String> remoteBuilder = bucketBuilderReturning(mock(BucketProxy.class));
        RateLimitService service = buildService(remoteBuilder);
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/items");
        request.setRemoteAddr("127.0.0.1");

        service.tryConsumeAnonymous(request);

        @SuppressWarnings("unchecked")
        ArgumentCaptor<Supplier<BucketConfiguration>> captor =
                ArgumentCaptor.forClass((Class) Supplier.class);
        verify(remoteBuilder).build(anyString(), captor.capture());
        Bandwidth[] limits = captor.getValue().get().getBandwidths();
        assertThat(limits).hasSize(1);
        assertThat(limits[0].getCapacity()).isEqualTo(2);
        assertThat(limits[0].getRefillTokens()).isEqualTo(3);
        assertThat(limits[0].getRefillPeriodNanos()).isEqualTo(Duration.ofMinutes(1).toNanos());
    }

    @Test
    void authenticatedBucketUsesItsOwnSettings() {
        RemoteBucketBuilder<String> remoteBuilder = bucketBuilderReturning(mock(BucketProxy.class));
        RateLimitService service = buildService(remoteBuilder);

        service.tryConsumeAuthenticated("42");

        @SuppressWarnings("unchecked")
        ArgumentCaptor<Supplier<BucketConfiguration>> captor =
                ArgumentCaptor.forClass((Class) Supplier.class);
        verify(remoteBuilder).build(anyString(), captor.capture());
        Bandwidth[] limits = captor.getValue().get().getBandwidths();
        assertThat(limits[0].getCapacity()).isEqualTo(4);
        assertThat(limits[0].getRefillTokens()).isEqualTo(5);
    }

    @Test
    void rejectionIsReturnedWhenBucketExhausted() {
        BucketProxy bucket = mock(BucketProxy.class);
        when(bucket.tryConsume(1)).thenReturn(false);
        RateLimitService service = buildService(bucketBuilderReturning(bucket));

        assertThat(service.tryConsumeAnonymous(new MockHttpServletRequest("GET", "/items"))).isFalse();
    }

    @Test
    void untrustedProxyKeysOnRemoteAddress() {
        RateLimitService service = buildService(bucketBuilderReturning(mock(BucketProxy.class)));
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/items");
        request.setRemoteAddr("203.0.113.9");
        request.addHeader("X-Forwarded-For", "198.51.100.23");

        assertThat(service.resolveClientIp(request)).isEqualTo("203.0.113.9");
    }

    @Test
    void trustedProxyUsesLastForwardedForEntry() {
        ProxyManager<String> proxyManager = mock(ProxyManager.class);
        RemoteBucketBuilder<String> remoteBuilder = bucketBuilderReturning(mock(BucketProxy.class));
        when(proxyManager.builder()).thenReturn(remoteBuilder);
        RateLimitService service = new RateLimitService(proxyManager, new RateLimitConfigProperties(
                new RateLimitConfigProperties.LimitSettings(3, 2),
                new RateLimitConfigProperties.LimitSettings(5, 4),
                List.of("203.0.113.7")));
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/items");
        request.setRemoteAddr("203.0.113.7");
        request.addHeader("X-Forwarded-For", "198.51.100.23, 203.0.113.7");

        assertThat(service.resolveClientIp(request)).isEqualTo("203.0.113.7");
    }

    @Test
    void privateProxyTrustsOnlyLastForwardedForEntry() {
        RateLimitService service = buildService(bucketBuilderReturning(mock(BucketProxy.class)));
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/items");
        request.setRemoteAddr("10.0.0.4");
        request.addHeader("X-Forwarded-For", "198.51.100.42, 10.0.0.4");

        assertThat(service.resolveClientIp(request)).isEqualTo("10.0.0.4");
    }

    @Test
    void cloudFrontViewerAddressTakesPrecedenceAndStripsPort() {
        RateLimitService service = buildService(bucketBuilderReturning(mock(BucketProxy.class)));
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/items");
        request.setRemoteAddr("10.0.0.4");
        request.addHeader("X-Forwarded-For", "198.51.100.42, 10.0.0.4");
        request.addHeader("CloudFront-Viewer-Address", "203.0.113.99:53049");

        assertThat(service.resolveClientIp(request)).isEqualTo("203.0.113.99");
    }

    @Test
    void cloudFrontViewerAddressStripsBracketsAndPortFromIpv6() {
        RateLimitService service = buildService(bucketBuilderReturning(mock(BucketProxy.class)));
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/items");
        request.setRemoteAddr("10.0.0.4");
        request.addHeader("CloudFront-Viewer-Address", "[2001:db8::1]:443");

        assertThat(service.resolveClientIp(request)).isEqualTo("2001:db8::1");
    }

    @Test
    void failedAuthBucketUsesSeparateKey() {
        BucketProxy bucket = mock(BucketProxy.class);
        RemoteBucketBuilder<String> remoteBuilder = bucketBuilderReturning(bucket);
        RateLimitService service = buildService(remoteBuilder);
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/items");
        request.setRemoteAddr("127.0.0.1");

        service.tryConsumeFailedAuth(request);

        verify(remoteBuilder).build(eq("failed:ip:127.0.0.1"), any(Supplier.class));
    }

    @Test
    void redisFailureFailsOpen() {
        ProxyManager<String> proxyManager = mock(ProxyManager.class);
        when(proxyManager.builder()).thenThrow(new RuntimeException("redis down"));
        RateLimitService service = new RateLimitService(proxyManager, CONFIG);

        assertThat(service.tryConsumeAnonymous(new MockHttpServletRequest("GET", "/items"))).isTrue();
    }
}