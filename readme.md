## routeswitchbysubnet for Autoscaling groups of Gateways

The purpose of the script **routeswitchbysubnet** is to allow, in AWS VPCs, the automatic reassignments of route-targets to healthy gateways, when these gateways are monitored by an ELB.
The typical use case is a Check Point vSEC Autoscaling group. By default these gateways are not configured as targets in any routes, and so they're not used for the protection of outbound traffic, except through a proxy. By using this script you'll be able to have the members configured as the default gateways for internet-bound (or other) traffic, and thus protect such traffic flow.
The way the script works is to maintain for each protected subnet a route table that points to a healthy gateway in the ASG. As gateways are added or removed, the script will detect it (in about 1 min) and will reassign route tables as necessary to the least used gateway. 



### Prerequisites and installation

- Have gateways (e.g., Check Point vSEC gateways) up and running as EC2 instances.  
- if the gateways have more than one ENI, make sure that they know to route to the subnets behind them through ethN where N is maximal. E.g., if the gateway has eth0 and eth1, make sure that each of the gateways knows that traffic to each of the protected subnets should be forwarded through eth1
- Set up an ELB to monitor the gateways. (the gateways can be part of an Autoscaling Group or not)
- Create an SNS topic and associate it with the following:
    - cloudwatch alarm associated with the ELB that will send notification if the number of unhealthy instances is larger than 0. Add to the alarm a notification, to the same topic, if the status changes back to OK
    - a notification set on the Autoscaling group itself whenever an instance is added or removed	
- Create a lambda function that uses the script routeswitchbysubnet.py, and have it triggered by notifications to that SNS topic 
- Add environment valiables to your function with the appropriate values:

| variable name | variable value| example |
|---|---|---|
| elbname | the name of the loadbalancer that's monitoring your gateways| myELB |
| inputsubnets | comma delimited list of the subnets that need to route through the gateways| subnet-xxx1, subnet-yyy2 |
| routetargets | comma delimited list of prefixes traffic to which needs to flow through the gateways | 0.0.0.0/0, 192.168.1.0/24 |



### Note to consider
currently Cloudwatch alerts must aggregate events at minimal time resultion of one minute. Thus this method of controlling routes will typically lag by one minute after a gateway first becomes "OutofService". More immdiate ways to trigger the the function RouteSwitchv2(elbname) would result in a more timely route modification


### What the script does
 
1) Create a list of all the Gateways that are associated with the ELB
2) find all the route tables that are already associated with the subnets that have these routes, and create route tables for the subnets for which it doesn't find 
3) for each such route table, it finds what's the AZ that's the most common among all the subnets that use this route table
4) for each such route table, it's trying to see if there's a gateway that's better matching for it, given the AZs of the subnets using the route table, and the load on the GW

![Alt Routeswitch diagram](/RouteTableRedistributionAlg.jpg?raw=true "RouteSwitchv2 flow")


